#!/usr/bin/env python3
"""analyze — the pre-registered JIT models over mine.py's rows.

  analyze.py <rows dir> <out.json>
"""

import datetime
import glob
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from sklearn.metrics import roc_auc_score
from statsmodels.genmod.bayes_mixed_glm import BinomialBayesMixedGLM

warnings.filterwarnings("ignore")
HELD_OUT = {"pallets__jinja", "pallets__werkzeug", "encode__starlette", "python-attrs__attrs",
            "marshmallow-code__marshmallow", "Textualize__textual", "aio-libs__aiohttp", "tqdm__tqdm",
            "pypa__virtualenv", "jd__tenacity"}
CUT = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
END = datetime.datetime(2025, 7, 1, tzinfo=datetime.timezone.utc).timestamp()
SIGNALS = ["complexity", "escapes", "duplication"]
BASE = "buggy ~ np.log1p(la) + np.log1p(ld) + np.log1p(nf) + entropy + np.log1p(exp)"
RNG = np.random.default_rng(20261024)


def load(rows):
    frames = []
    for path in sorted(glob.glob(os.path.join(rows, "*.csv"))):
        frame = pd.read_csv(path)
        frame["repo"] = os.path.basename(path)[:-4]
        frames.append(frame)
    data = pd.concat(frames, ignore_index=True)
    data["held_out"] = data.repo.isin(HELD_OUT)
    data["split"] = np.where(data.ct < CUT, "train", np.where(data.ct < END, "test", "censored"))
    return data


def cluster_boot_auc_delta(test, p_base, p_full, reps=1000):
    repos = test.repo.unique()
    index = {r: np.flatnonzero(test.repo.values == r) for r in repos}
    y = test.buggy.values
    deltas = []
    for _ in range(reps):
        pick = np.concatenate([index[r] for r in RNG.choice(repos, len(repos))])
        if len(set(y[pick])) < 2:
            continue
        deltas.append(roc_auc_score(y[pick], p_full[pick]) - roc_auc_score(y[pick], p_base[pick]))
    return [float(np.percentile(deltas, 2.5)), float(np.percentile(deltas, 97.5))]


def fixed_effects(train, formula):
    model = smf.logit(formula + " + C(repo)", train).fit(
        disp=0, cov_type="cluster", cov_kwds={"groups": pd.factorize(train.repo)[0]}, maxiter=200)
    return model


def mixed(train, formula):
    model = BinomialBayesMixedGLM.from_formula(formula, {"repo": "0 + C(repo)"}, train)
    return model.fit_vb()


def odds(model, name, bayes=False):
    if bayes:
        names = list(model.model.exog_names)
        i = names.index(name)
        mean, sd = model.fe_mean[i], model.fe_sd[i]
        return dict(odds=float(np.exp(mean)), ci=[float(np.exp(mean - 1.96 * sd)), float(np.exp(mean + 1.96 * sd))])
    lo, hi = model.conf_int().loc[name]
    return dict(odds=float(np.exp(model.params[name])), ci=[float(np.exp(lo)), float(np.exp(hi))],
                p=float(model.pvalues[name]))


def evaluate(fit_set, test_set, pooled=False):
    """AUC of the base model and of base + each signal, fitted on fit_set, scored on test_set."""
    out = {}
    def predict(formula):
        if pooled:
            model = smf.logit(formula, fit_set).fit(disp=0, maxiter=200)
            return model.predict(test_set).values
        model = smf.logit(formula + " + C(repo)", fit_set).fit(disp=0, maxiter=200)
        return model.predict(test_set).values
    p_base = predict(BASE)
    out["base_auc"] = float(roc_auc_score(test_set.buggy, p_base))
    for s in SIGNALS + ["all"]:
        formula = BASE + " + " + (" + ".join(SIGNALS) if s == "all" else s)
        p_full = predict(formula)
        auc = float(roc_auc_score(test_set.buggy, p_full))
        out[s] = dict(auc=auc, delta=auc - out["base_auc"],
                      delta_ci=cluster_boot_auc_delta(test_set.reset_index(drop=True), p_base, p_full))
    return out


def describe(data):
    return {split: dict(commits=int(len(g)), buggy=float(g.buggy.mean()), repos=int(g.repo.nunique()),
                        **{s: float(g[s].mean()) for s in SIGNALS})
            for split, g in data.groupby(data.held_out.map({False: "reference", True: "held_out"}) + "/" + data.split)}


def main(rows, out_path):
    data = load(rows)
    ref = data[~data.held_out]
    train, test = ref[ref.split == "train"], ref[ref.split == "test"]
    held = data[data.held_out]
    result = dict(describe=describe(data), odds={}, odds_mixed={}, odds_held_out={}, odds_joint={})
    for s in SIGNALS:
        result["odds"][s] = odds(fixed_effects(train, BASE + " + " + s), s)
        result["odds_mixed"][s] = odds(mixed(train, BASE + " + " + s), s, bayes=True)
        result["odds_held_out"][s] = odds(fixed_effects(held[held.split == "train"], BASE + " + " + s), s)
    joint = fixed_effects(train, BASE + " + " + " + ".join(SIGNALS))
    result["odds_joint"] = {s: odds(joint, s) for s in SIGNALS}
    result["test_window"] = evaluate(train, test)
    result["held_out_window"] = evaluate(train, held[held.split == "test"], pooled=True)
    result["reference_window_pooled"] = evaluate(train, test, pooled=True)
    with open(out_path, "w") as handle:
        json.dump(result, handle, indent=1)
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])

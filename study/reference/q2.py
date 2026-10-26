#!/usr/bin/env python3
"""q2 — Alves/SIG thresholds and repository-bootstrap intervals for the summary's percentiles.

  q2.py <remeasure q2 dir> <shipped reference.json> <out.json>
"""

import glob
import os
import json
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "quality", "bin"))
import reference   # noqa: E402  cleat's own standing()

RNG = np.random.default_rng(20261024)
REPS = 2000
QUANTS = (0.70, 0.80, 0.90, 0.95)


def load(directory):
    out = []
    for path in sorted(glob.glob(directory + "/*.json")):
        with open(path) as handle:
            d = json.load(handle)
        if "error" not in d:
            out.append(d)
    return out


def alves(results, language, column):
    """LOC-weighted quantiles, each system weighing the same (Alves, Ypma, Visser 2010)."""
    values, weights = [], []
    for d in results:
        funcs = [f for f in d.get("functions", {}).get(language, []) if f[2] > 0]
        total = sum(f[2] for f in funcs)
        if not total:
            continue
        for f in funcs:
            values.append(f[column])
            weights.append(f[2] / total)
    if not values:
        return None
    order = np.argsort(values, kind="stable")
    v, w = np.array(values)[order], np.array(weights)[order]
    cum = np.cumsum(w) / w.sum()
    systems = sum(1 for d in results if d.get("functions", {}).get(language))
    return dict(systems=systems, functions=len(values),
                **{"q%d" % round(q * 100): float(v[np.searchsorted(cum, q)]) for q in QUANTS})


def standing_ci(results, language, metric, value):
    repos = [[p[metric] for p in d["parts"] if p.get("language") == language and p.get(metric) is not None]
             for d in results]
    repos = [r for r in repos if r]
    if len(repos) < 2:
        return None
    flat = sorted(x for r in repos for x in r)
    point = reference.standing({"languages": {language: {metric: flat}}}, language, metric, value)
    shares = []
    for _ in range(REPS):
        pick = [x for i in RNG.integers(0, len(repos), len(repos)) for x in repos[i]]
        s = reference.standing({"languages": {language: {metric: sorted(pick)}}}, language, metric, value)
        if s:
            shares.append(s[0])
    if not shares:
        return None
    lo, hi = np.percentile(shares, [2.5, 97.5])
    return dict(value=value, standing=point[0] if point else None, parts=len(flat), repos=len(repos),
                ci=[float(lo), float(hi)], width=float(hi - lo))


def main(directory, shipped_path, out_path):
    results = load(directory)
    with open(shipped_path) as handle:
        shipped = json.load(handle)
    out = dict(repos_measured=len(results), alves={}, intervals={}, drift={})
    langs = sorted(shipped["languages"])
    for lang in langs:
        out["alves"][lang] = dict(cc=alves(results, lang, 0), length=alves(results, lang, 1))
        out["intervals"][lang] = {}
        for metric in reference.METRICS:
            table = shipped["languages"][lang].get(metric) or []
            fresh = sorted(p[metric] for d in results for p in d["parts"]
                           if p.get("language") == lang and p.get(metric) is not None)
            out["drift"].setdefault(lang, {})[metric] = dict(shipped=len(table), fresh=len(fresh),
                                                             shipped_median=float(np.median(table)) if table else None,
                                                             fresh_median=float(np.median(fresh)) if fresh else None)
            if len(fresh) < reference.MIN_PARTS:
                continue
            rows = {}
            for q in (0.1, 0.25, 0.5, 0.75, 0.9):
                rows["p%d" % round(q * 100)] = standing_ci(results, lang, metric, float(np.quantile(fresh, q)))
            out["intervals"][lang][metric] = rows
    with open(out_path, "w") as handle:
        json.dump(out, handle, indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])

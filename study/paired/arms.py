#!/usr/bin/env python3
"""arms — the three-arm runs of 2026-11-02 against their pre-registered counts.

  arms.py <work dir> [arm …]

Per arm, over every task with all its runs: tasks resolved per repeat, runs ending
with cleat's real gates failing (and with a mined verdict failing, where the language
has them), median cost and turns. Arms default to plain, cleat and fit.
"""

import glob
import json
import os
import statistics
import sys

VERDICTS = ("complexity", "escapes", "duplication")


def load(work):
    runs = {}
    for path in glob.glob(os.path.join(work, "runs", "*", "*", "score.json")):
        task, name = path.split(os.sep)[-3:-1]
        runs.setdefault(task, {})[name] = json.load(open(path))["score"]
    return runs


def main(work, *arms):
    arms = arms or ("plain", "cleat", "fit")
    runs = load(work)
    repeats = sorted({n.split("-")[1] for r in runs.values() for n in r if "-" in n})
    names = ["%s-%s" % (a, k) for a in arms for k in repeats]
    complete = {t: r for t, r in runs.items() if all(n in r for n in names)}
    print("tasks with every run: %d of %d; repeats %s" % (len(complete), len(runs), ",".join(repeats)))
    out = {}
    for arm in arms:
        mine = [complete[t]["%s-%s" % (arm, k)] for t in complete for k in repeats]
        mined = [s["mined"] for s in mine if s.get("mined") and s["mined"].get("complexity") is not None]
        row = dict(
            resolved=[sum(complete[t]["%s-%s" % (arm, k)]["resolved"] for t in complete) for k in repeats],
            gate=sum(bool(s["real_gate"]["failing"]) for s in mine),
            mined=sum(any(m[v] for v in VERDICTS) for m in mined) if mined else None,
            runs=len(mine),
            cost=statistics.median(s["run"].get("total_cost_usd") or 0 for s in mine),
            total=sum(s["run"].get("total_cost_usd") or 0 for s in mine),
            turns=statistics.median(s["run"].get("num_turns") or 0 for s in mine))
        out[arm] = row
        print("  %-6s resolved %-9s real gate failing %2d of %d%s  median $%.2f, %s turns  (total $%.2f)" % (
            arm, "/".join(map(str, row["resolved"])), row["gate"], row["runs"],
            "" if row["mined"] is None else ", mined %2d" % row["mined"], row["cost"], row["turns"], row["total"]))
    with open(os.path.join(work, "arms-summary.json"), "w") as handle:
        json.dump(dict(tasks=len(complete), repeats=repeats, arms=out), handle, indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])

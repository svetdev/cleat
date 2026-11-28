#!/usr/bin/env python3
"""report — read the chains of a work dir: what each request broke, and what it cost.

  report.py <work dir> [chain …]

One line per finished step: whether the feature asked for passes its own hidden tests,
how many of the features so far pass, and which earlier features it broke (`regressed`:
passed at the step before, fail now) or brought back (`mended`). Then per chain, and per
arm over its chains: steps, steps that broke something, features broken in all, features
passing at the last step, the cost, and the codebase's lines. The main outcome is
regressions per step.
"""

import collections
import json
import os
import sys


def steps(home):
    found = []
    for name in sorted(os.listdir(os.path.join(home, "steps"))) if os.path.isdir(os.path.join(home, "steps")) else []:
        path = os.path.join(home, "steps", name, "score.json")
        if os.path.isfile(path):
            with open(path) as handle:
                found.append(json.load(handle))
    return found


def totals(scores):
    last = scores[-1]
    return dict(steps=len(scores), breaking=sum(bool(s["regressed"]) for s in scores), broken=sum(len(s["regressed"]) for s in scores),
                own=sum(s["own"] for s in scores), passing=last["passing"], of=last["feature"], cost=sum(s["cost"] or 0 for s in scores),
                lines=last["code_lines"], test_lines=last["test_lines"])


def line(name, t):
    return ("%-10s steps %3d  breaking %3d  features broken %3d  own passed %3d  passing at the end %2d/%-2d  $%7.2f  %5d lines (%d of tests)"
            % (name, t["steps"], t["breaking"], t["broken"], t["own"], t["passing"], t["of"], t["cost"], t["lines"], t["test_lines"]))


def main(work, *chains):
    root = os.path.join(os.path.abspath(work), "chains")
    arms = collections.defaultdict(list)
    for name in chains or sorted(os.listdir(root)):
        scores = steps(os.path.join(root, name))
        for s in scores:
            opened = s["opened"]["share"]
            print("%-10s %02d %-34s own %-5s passing %2d/%-2d regressed %-12s mended %-8s $%.2f  %3s turns  %5d lines  opened %s"
                  % (name, s["feature"], s["name"][:34], s["own"], s["passing"], s["feature"], s["regressed"], s["mended"], s["cost"] or 0,
                     s["turns"], s["code_lines"], "-" if opened is None else "%d%%" % round(opened * 100)))
        if scores:
            arms[name.split("-")[0]].append(totals(scores))
    for arm, each in sorted(arms.items()):
        summed = {key: sum(t[key] for t in each) for key in each[0]}
        print(line("%s x%d" % (arm, len(each)), summed))


if __name__ == "__main__":
    main(*sys.argv[1:])

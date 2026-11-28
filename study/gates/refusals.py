#!/usr/bin/env python3
"""refusals — what history.py's rows say: how many merged commits each gate refused, and why.

  refusals.py <dir of history.py's <repo>.json>

Per repository: commits replayed, commits refused and by which gate. Then, over all of them:
a complexity refusal is a new function over the ceiling, a baselined one that grew, or both;
a duplication or escapes refusal names a test file or production only. Last, the growth
rules: of the refusals that name only baselined functions that grew, how many each allowance
would have let through. That table is how `complexity.growth` was chosen (DECISIONS.md,
2026-11-19). No agent and no tokens.
"""

import collections
import glob
import json
import os
import re
import statistics
import sys

WORSE = re.compile(r"cc (\d+)[^,]*, (\d+) lines, was cc (\d+)[^,]*, (\d+) lines")
TEST = re.compile(r"tests?/|test_|_test|\.test\.|\.spec\.|Tests?\.(swift|kt)|/androidTest/")
RULES = [
    ("none (the ratchet as it was)", lambda cc, lines, was_cc, was_lines: cc <= was_cc and lines <= was_lines),
    ("lines free, cc exact", lambda cc, lines, was_cc, was_lines: cc <= was_cc),
    ("+1 cc, +5 lines", lambda cc, lines, was_cc, was_lines: cc <= was_cc + 1 and lines <= was_lines + 5),
    ("10% (at least 1 cc, 5 lines)", lambda cc, lines, was_cc, was_lines: cc <= was_cc + max(1, was_cc // 10) and lines <= was_lines + max(5, was_lines // 10)),
    ("+2 cc, +10 lines", lambda cc, lines, was_cc, was_lines: cc <= was_cc + 2 and lines <= was_lines + 10),
    ("20% (at least 1 cc, 5 lines)", lambda cc, lines, was_cc, was_lines: cc <= was_cc + max(1, was_cc // 5) and lines <= was_lines + max(5, was_lines // 5)),
]


def said_by(row, gate):
    """The gate's own lines in what gate.py printed for the commit."""
    text = row.get("said", "")
    if "FAIL  " + gate not in text:
        return ""
    return text.split("FAIL  " + gate)[1].split("\n  ok")[0].split("\n  FAIL  ")[0]


def complexity_kind(section):
    """('new' | 'grew' | 'both', [(cc, lines, was cc, was lines)] of the baselined functions that grew)."""
    grew = [tuple(int(n) for n in m) for m in WORSE.findall(section.split("NOTE")[0])]
    new = "new production function" in section
    return ("both" if new and grew else "new" if new else "grew"), grew


def main(directory):
    kinds, only_grew, deltas, total, refused_total = collections.Counter(), [], [], 0, 0
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        rows = json.load(open(path))
        refused = [r for r in rows if r.get("failing")]
        by_gate = collections.Counter(g for r in refused for g in r["failing"])
        print("%-26s %4d commits  refused %3d (%2.0f%%)  %s" % (
            os.path.basename(path)[:-5], len(rows), len(refused), 100 * len(refused) / max(len(rows), 1), dict(by_gate)))
        total, refused_total = total + len(rows), refused_total + len(refused)
        for row in refused:
            if "complexity" in row["failing"]:
                kind, grew = complexity_kind(said_by(row, "complexity"))
                kinds["complexity: " + {"new": "a new function over the ceiling", "grew": "a baselined function grew", "both": "both"}[kind]] += 1
                deltas += [(cc - was_cc, lines - was_lines) for cc, lines, was_cc, was_lines in grew]
                if kind == "grew":
                    only_grew.append(grew)
            for gate in ("duplication", "escapes"):
                if gate in row["failing"]:
                    kinds["%s: %s" % (gate, "names a test file" if TEST.search(said_by(row, gate)) else "production only")] += 1
            for gate in row["failing"]:
                if gate not in ("complexity", "duplication", "escapes"):
                    kinds[gate] += 1
    print("\n%d commits, %d refused (%.0f%%)" % (total, refused_total, 100 * refused_total / max(total, 1)))
    for kind, count in sorted(kinds.items()):
        print("  %4d  %s" % (count, kind))
    if deltas:
        print("baselined functions that grew: %d, median +%s cc and +%s lines" % (
            len(deltas), statistics.median(d[0] for d in deltas), statistics.median(d[1] for d in deltas)))
    if only_grew:
        print("\nof the %d refusals that name only baselined functions that grew, an allowance would pass:" % len(only_grew))
        for name, rule in RULES:
            print("  %-30s %3d" % (name, sum(all(rule(*f) for f in grew) for grew in only_grew)))


if __name__ == "__main__":
    main(*sys.argv[1:])

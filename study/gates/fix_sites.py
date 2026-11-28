#!/usr/bin/env python3
"""fix_sites — when a refused commit is later fixed, does the fix land on the code the gate named?

  fix_sites.py <dir of history.py's <repo>.json> <clones dir> [<clones dir> …]

later_fixes.py says a refused commit is fixed later more often than a passed one of its size.
That holds whether the gates point at the defect or only mark a risky commit. Here, for each
refused commit: the production lines it added; among them the lines a gate named (a function
over the ceiling from its first line to its last, both copies of a clone, the line of an
escape); and the lines a later fix removed or rewrote (blamed at the fix's parent, as
later_fixes.py does, and numbered as the refused commit numbered them). If the gates point at
the defect, the fixed lines are named lines more often than the commit's lines are.

Printed per gate and for all three: commits with a fix on lines they added, the share of
added lines that were named, the share of fixed lines that were named, and how many fixed
lines would be named by chance. Chance keeps each commit's fixed lines together and moves
them as one block to a random place among the lines the commit added, since a fix rewrites
neighbouring lines and they are not independent. No agent and no tokens.
"""

import collections
import glob
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import later_fixes as lf   # noqa: E402

GATE_LINE = re.compile(r"^  (?:FAIL|ok)\s+(\S+)")
FUNCTION = re.compile(r"^\s+(\S+):(\d+)  cc \d+, (\d+) lines")
CLONE = re.compile(r"(\S+):(\d+)-(\d+)")
ESCAPE = re.compile(r"^\s+(\S+):(\d+)  \S")
ADDED = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def spans(gate, line):
    """[(file, first, last)] one line of a gate's output names."""
    if gate == "complexity":
        m = FUNCTION.match(line)
        return [(m.group(1), int(m.group(2)), int(m.group(2)) + int(m.group(3)) - 1)] if m else []
    if gate == "duplication":
        return [(f, int(a), int(b)) for f, a, b in CLONE.findall(line)] if re.match(r"^\s+\d+ lines  ", line) else []
    m = ESCAPE.match(line)
    return [(m.group(1), int(m.group(2)), int(m.group(2)))] if m else []


def named(said):
    """{gate: {(file, line)}} the gates' own output names."""
    out, gate = collections.defaultdict(set), None
    for line in said.splitlines():
        head = GATE_LINE.match(line)
        if head:
            gate = head.group(1) if line.startswith("  FAIL") and head.group(1) in lf.GATES else None
        elif gate:
            for name, first, last in spans(gate, line):
                out[gate].update((name, n) for n in range(first, last + 1))
    return out


def added(repo, sha):
    """{(file, line)} of the production lines the commit added, as it numbers them."""
    out, name = set(), None
    for line in lf.git(repo, "show", "--format=", "--unified=0", "--no-renames", sha).splitlines():
        if line.startswith("+++ "):
            name = None if line == "+++ /dev/null" else line[6:]
        elif line.startswith("@@") and name and lf.production(name):
            m = ADDED.match(line)
            out.update((name, n) for n in range(int(m.group(1)), int(m.group(1)) + int(m.group(2) or 1)))
    return out


def fixed_lines(repo, oldest, wanted):
    """{commit: {(file, line)}}: the lines of each wanted commit that a fix after `oldest` removed or rewrote."""
    hit = collections.defaultdict(set)
    for row in lf.git(repo, "log", "--first-parent", "--no-merges", "--format=%H\t%s", oldest + "..HEAD").splitlines():
        sha, _, subject = row.partition("\t")
        if not lf.FIX.search(subject):
            continue
        for name, ranges in lf.removed(repo, sha).items():
            args = [arg for start, count in ranges for arg in ("-L", "%d,+%d" % (start, count))]
            origin = line_no = None
            for line in lf.git(repo, "blame", "--line-porcelain", *args, sha + "^", "--", name).splitlines():
                head = re.match(r"^([0-9a-f]{40}) (\d+) \d+", line)
                if head:
                    origin, line_no = head.group(1), int(head.group(2))
                elif line.startswith("filename ") and origin in wanted:
                    hit[origin].add((line[9:], line_no))
    return hit


def shifted(rng, flags, fixed):
    """How many named lines the fixed lines would cover, moved together to a random place in the commit."""
    by = rng.randrange(len(flags))
    return sum(flags[(i + by) % len(flags)] for i in fixed)


def report(label, rows, rounds=2000):
    """rows: per commit with a fix on its lines, (whether each added line was named, in file
    and line order; the positions a fix landed on). The fixed lines of a commit sit together,
    so chance is the same lines moved as one block around the commit, not drawn one by one."""
    rows = [(flags, fixed) for flags, fixed in rows if any(flags)]
    if not rows:
        print("%-12s none" % label)
        return
    rng = random.Random(20261122)
    seen = sum(flags[i] for flags, fixed in rows for i in fixed)
    drawn = [sum(shifted(rng, *row) for row in rows) for _ in range(rounds)]
    add, add_named, fix = sum(len(f) for f, _ in rows), sum(sum(f) for f, _ in rows), sum(len(x) for _, x in rows)
    print("%-12s %3d commits | named %2.0f%% of the lines added and %2.0f%% of the lines fixed | fixed lines named %d, by chance %.0f "
          "(as many or more in %.3f of %d shuffles) | a fix on a named line in %d commits" % (
              label, len(rows), 100 * add_named / add, 100 * seen / fix, seen, sum(drawn) / rounds,
              sum(d >= seen for d in drawn) / rounds, rounds, sum(any(flags[i] for i in fixed) for flags, fixed in rows)))


def main(directory, *clone_dirs):
    by_gate, detail = collections.defaultdict(list), []
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        name = os.path.basename(path)[:-5]
        repo = next((os.path.join(d, name) for d in clone_dirs if os.path.isdir(os.path.join(d, name))), None)
        rows = [r for r in json.load(open(path)) if "failing" in r]
        refused = {r["sha"]: r for r in rows if set(r["failing"]) & set(lf.GATES)}
        if not repo or not refused:
            continue
        hits = fixed_lines(repo, rows[-1]["sha"], refused)
        for sha, hit in hits.items():
            mine, flagged = added(repo, sha), named(refused[sha]["said"])
            hit &= mine
            if not hit:
                continue
            everything, order = set().union(*flagged.values()) & mine, sorted(mine)
            fixed = [i for i, line in enumerate(order) if line in hit]
            by_gate["any gate"].append(([line in everything for line in order], fixed))
            for gate, lines in flagged.items():
                by_gate[gate].append(([line in lines for line in order], fixed))
            detail.append(dict(repo=name, sha=sha, added=len(mine), named=len(everything), fixed=len(hit), fixed_named=len(hit & everything)))
        print("%-26s refused %3d, with a fix on lines they added %3d" % (name, len(refused), sum(1 for d in detail if d["repo"] == name)), flush=True)
    print()
    for label in ("any gate",) + lf.GATES:
        report(label, by_gate[label])
    if os.environ.get("STUDY_OUT"):
        with open(os.environ["STUDY_OUT"], "w") as handle:
            json.dump(detail, handle, indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])

#!/usr/bin/env python3
"""later_fixes — are the commits the gates refuse the ones that later needed fixing?

  later_fixes.py <dir of history.py's <repo>.json> <clones dir> [<clones dir> …]

history.py says which merged commits the gates would have refused. Run it on a stretch that
ends well back (its `newest commit` argument), so fixes have had time to land. Here, for
each repository: every later first-parent commit whose subject says fix, bug, regression,
crash or panic is a fix; each production line a fix removed or rewrote is blamed at the fix's
parent, and the commit that wrote it is one a fix later landed on (the SZZ rule, as
study/jit/mine.py applies it to Python). Then, over the commits history.py replayed: the share
later fixed among the refused against the passed, by gate, and again within commits of like
size, since a larger commit is both refused and fixed more often; then one odds ratio with
repository and size held fixed together (Mantel-Haenszel over repository × size third).
Last, matched pairs: each refused commit beside a passed commit of the same repository whose
lines added are within a factor of 1.25, each passed commit used once, and a sign test over
the pairs where only one of the two was later fixed. That is the comparison to trust: the
thirds still hold commits of very different size.
Refused means refused by complexity, duplication or escapes: a document over its ceiling
says nothing about the code. No agent and no tokens.
"""

import collections
import glob
import json
import math
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "quality", "bin"))
import summary   # noqa: E402  cleat's own test-file rule

FIX = re.compile(r"\b(fix(es|ed)?|bug|regression|crash|panic)\b", re.I)
SOURCE = (".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".swift", ".kt", ".java", ".rb")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
GATES = ("complexity", "duplication", "escapes")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, errors="replace").stdout


def production(name):
    return name.endswith(SOURCE) and not summary._is_test(name)


def removed(repo, sha):
    """{file: [(first line, count)]} of the production lines the commit removed or rewrote, as numbered in its parent."""
    out, name = collections.defaultdict(list), None
    for line in git(repo, "show", "--format=", "--unified=0", "--no-renames", sha).splitlines():
        if line.startswith("--- "):
            name = None if line == "--- /dev/null" else line[6:]
        elif line.startswith("@@") and name and production(name):
            m = HUNK.match(line)
            if int(m.group(2) or 1):
                out[name].append((int(m.group(1)), int(m.group(2) or 1)))
    return out


def fixed_by_later(repo, oldest):
    """The commits a fix after `oldest` landed on."""
    hit = set()
    for row in git(repo, "log", "--first-parent", "--no-merges", "--format=%H\t%s", oldest + "..HEAD").splitlines():
        sha, _, subject = row.partition("\t")
        if not FIX.search(subject):
            continue
        for name, spans in removed(repo, sha).items():
            ranges = [arg for start, count in spans for arg in ("-L", "%d,+%d" % (start, count))]
            for line in git(repo, "blame", "-l", "-s", *ranges, sha + "^", "--", name).splitlines():
                hit.add(line.split()[0].lstrip("^"))
    return hit


def added_lines(row):
    m = re.search(r"(\d+) insertion", row.get("stat", ""))
    return int(m.group(1)) if m else 0


def rate(rows):
    return "%3d of %3d (%2.0f%%)" % (sum(r["fixed"] for r in rows), len(rows), 100 * sum(r["fixed"] for r in rows) / len(rows)) if rows else "  none"


def pooled_odds(rows):
    """The Mantel-Haenszel odds ratio of later fixed for refused against passed, over strata of
    repository and size third within it; and how many strata could tell (both kinds present)."""
    strata = collections.defaultdict(list)
    for repo in {r["repo"] for r in rows}:
        ordered = sorted((r for r in rows if r["repo"] == repo), key=added_lines)
        for i, row in enumerate(ordered):
            strata[(repo, 3 * i // len(ordered))].append(row)
    top = bottom = used = 0
    for part in strata.values():
        a = sum(r["fixed"] and bool(r["failing"]) for r in part)
        b = sum(not r["fixed"] and bool(r["failing"]) for r in part)
        c = sum(r["fixed"] and not r["failing"] for r in part)
        d = sum(not r["fixed"] and not r["failing"] for r in part)
        if (a + b) and (c + d):
            used, top, bottom = used + 1, top + a * d / len(part), bottom + b * c / len(part)
    return (top / bottom if bottom else float("inf")), used


def matched(rows, factor=1.25):
    """[(refused commit, passed commit)] of one repository and like size, each passed commit used once."""
    pairs = []
    for repo in sorted({r["repo"] for r in rows}):
        passed = [r for r in rows if r["repo"] == repo and not r["failing"]]
        for row in sorted((r for r in rows if r["repo"] == repo and r["failing"]), key=added_lines):
            size = max(added_lines(row), 1)
            near = [p for p in passed if size / factor <= max(added_lines(p), 1) <= size * factor]
            if near:
                twin = min(near, key=lambda p: abs(added_lines(p) - size))
                passed.remove(twin)
                pairs.append((row, twin))
    return pairs


def sign_test(more, fewer):
    """Two-sided: how likely a split at least this uneven is when either side is as likely."""
    n, k = more + fewer, max(more, fewer)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n) if n else 1.0


def report_matched(rows):
    pairs = matched(rows)
    only_refused = sum(a["fixed"] and not b["fixed"] for a, b in pairs)
    only_passed = sum(b["fixed"] and not a["fixed"] for a, b in pairs)
    print("  matched pairs of like size: %d; later fixed, refused %d and passed %d; only the refused one %d, only the passed one %d (sign test p %.3f)" % (
        len(pairs), sum(a["fixed"] for a, _ in pairs), sum(b["fixed"] for _, b in pairs), only_refused, only_passed, sign_test(only_refused, only_passed)))
    for gate in GATES:
        mine = [(a, b) for a, b in pairs if gate in a["failing"]]
        print("    refused by %-11s %3d pairs; later fixed, refused %d and passed %d" % (gate, len(mine), sum(a["fixed"] for a, _ in mine), sum(b["fixed"] for _, b in mine)))


def report(rows):
    print("\n%d commits, %d later landed on by a fix" % (len(rows), sum(r["fixed"] for r in rows)))
    print("  odds of a later fix, refused against passed, repository and size held fixed: %.2f (%d strata)" % pooled_odds(rows))
    print("  refused by any gate   %s | passed %s" % (rate([r for r in rows if r["failing"]]), rate([r for r in rows if not r["failing"]])))
    report_matched(rows)
    for gate in GATES:
        print("  refused by %-11s %s | not    %s" % (gate, rate([r for r in rows if gate in r["failing"]]), rate([r for r in rows if gate not in r["failing"]])))
    ordered = sorted(rows, key=added_lines)
    third = len(ordered) // 3
    for label, part in (("smallest third", ordered[:third]), ("middle third", ordered[third:2 * third]), ("largest third", ordered[2 * third:])):
        print("  %-15s (up to %4d lines added)  refused %s | passed %s" % (
            label, added_lines(part[-1]) if part else 0, rate([r for r in part if r["failing"]]), rate([r for r in part if not r["failing"]])))


def main(directory, *clone_dirs):
    everything = []
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        name = os.path.basename(path)[:-5]
        repo = next((os.path.join(d, name) for d in clone_dirs if os.path.isdir(os.path.join(d, name))), None)
        rows = [r for r in json.load(open(path)) if "failing" in r]
        if not repo or not rows:
            continue
        hit = fixed_by_later(repo, rows[-1]["sha"])   # history.py lists the newest first
        for row in rows:
            row["fixed"], row["repo"] = row["sha"] in hit, name
            row["failing"] = [g for g in row["failing"] if g in GATES]
        everything += rows
        print("%-26s %3d commits  refused %s | passed %s" % (
            name, len(rows), rate([r for r in rows if r["failing"]]), rate([r for r in rows if not r["failing"]])), flush=True)
    report(everything)


if __name__ == "__main__":
    main(*sys.argv[1:])

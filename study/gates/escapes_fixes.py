#!/usr/bin/env python3
"""escapes_fixes — do bug-fix commits change lines that carry an escape more than other commits do?

  escapes_fixes.py <out.json> <clone>:<language> [<clone>:<language> …]      (STUDY_COMMITS, default 1500)

Per clone (bare or not), over its last first-parent commits: the production lines each commit
removed or rewrote, and how many of them carried one of cleat's escape patterns for the
language (`check-escapes.py --list-languages`), for commits whose subject says fix, bug,
regression, crash or panic against the rest; and the same share over every production line at
HEAD. If escapes hid bugs, fixes would land on them more often than on other lines. Test files
are left out by cleat's own rule; a skipped test is not counted. No agent and no tokens.
"""

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "quality", "bin"))
import summary                    # noqa: E402  cleat's own test-file rule
from extractors import languages  # noqa: E402

FIX = re.compile(r"\b(fix(es|ed)?|bug|regression|crash|panic)\b", re.I)
COMMITS = os.environ.get("STUDY_COMMITS", "1500")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, errors="replace").stdout


def removed(repo, sha, production):
    """The production lines the commit removed or rewrote, as text."""
    out, name = [], None
    for line in git(repo, "show", "--format=", "--unified=0", "--no-renames", sha).splitlines():
        if line.startswith("--- "):
            name = None if line == "--- /dev/null" else line[6:]
        elif line.startswith("-") and not line.startswith("---") and name and production(name):
            out.append(line[1:])
    return out


def tree(repo, production):
    """Every non-blank production line at HEAD."""
    for name in git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines():
        if production(name):
            yield from (line for line in git(repo, "show", "HEAD:" + name).splitlines() if line.strip())


def one(repo, language):
    spec = languages.language(language)
    suffixes = tuple(spec["suffixes"])
    patterns = [re.compile(p) for kind, p in spec["patterns"].items() if kind != "skipped test"]
    production = lambda name: name.endswith(suffixes) and not summary._is_test(name)   # noqa: E731
    escape = lambda line: any(p.search(line) for p in patterns)                        # noqa: E731
    tally = {True: [0, 0, 0], False: [0, 0, 0]}   # commits, lines, lines with an escape
    for row in git(repo, "log", "--first-parent", "--no-merges", "-n", COMMITS, "--format=%H\t%s").splitlines():
        sha, _, subject = row.partition("\t")
        lines = removed(repo, sha, production)
        if lines:
            mine = tally[bool(FIX.search(subject))]
            mine[0], mine[1], mine[2] = mine[0] + 1, mine[1] + len(lines), mine[2] + sum(escape(l) for l in lines)
    whole = [0, 0]
    for line in tree(repo, production):
        whole[0], whole[1] = whole[0] + 1, whole[1] + escape(line)
    return dict(fix=tally[True], other=tally[False], tree=whole)


def share(lines, escapes):
    return "%.2f%%" % (100 * escapes / lines) if lines else "n/a"


def main(out, *pairs):
    got, pooled = {}, dict(fix=[0, 0, 0], other=[0, 0, 0], tree=[0, 0])
    for pair in pairs:
        repo, _, language = pair.rpartition(":")
        row = got[os.path.basename(os.path.normpath(repo))] = one(repo, language)
        for key, values in row.items():
            pooled[key] = [a + b for a, b in zip(pooled[key], values)]
        print("%-26s fix %4d commits %7d lines %s | other %4d commits %7d lines %s | tree %s" % (
            os.path.basename(os.path.normpath(repo)), *row["fix"][:2], share(*row["fix"][1:]),
            *row["other"][:2], share(*row["other"][1:]), share(*row["tree"])), flush=True)
    print("pooled: fix %s of %d lines, other %s of %d, tree %s" % (
        share(*pooled["fix"][1:]), pooled["fix"][1], share(*pooled["other"][1:]), pooled["other"][1], share(*pooled["tree"])))
    with open(out, "w") as handle:
        json.dump(dict(repos=got, pooled=pooled), handle, indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])

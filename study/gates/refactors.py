#!/usr/bin/env python3
"""refactors — after maintainers split a function that was over the ceiling, is it fixed less often?

  refactors.py <out dir> <clones dir> [<clones dir> …]

No agent and no tokens. Per repository, over every commit that is not a merge (a project that
merges pull requests keeps its work off the first-parent line): a split is a commit
whose subject does not say fix (later_fixes.py's rule), that either touches no test file or
says refactor, extract, split, simplify or clean up, and in which a function over cleat's
ceiling at the parent (cyclomatic over 8 or longer than 60 lines, by lizard, as the
complexity gate reads it) is under it afterwards, in a file that gains a function it did not
have. Only splits with DAYS of history on each side are kept.

For each split function, the fixes that touched it are counted in the DAYS before the split
and the DAYS after: a fix is a commit whose subject says fix, bug, regression, crash or
panic, and it touches a function when one of its hunks, as numbered in the fix's parent,
falls inside the function's lines there. After the split the function is counted twice: with
the functions the split added to the file, found by name, which is more code than before and
some of it new; and by itself, which is less. Fixes are also given per 1,000 lines followed.
A function not yet in the file DAYS before could not have been fixed then, so the report is
repeated over the functions that were. Beside each split, up to CONTROLS functions over the
ceiling at the same commit, drawn with a seed from PICK of the repository's production files
and not split by it, are counted the same way: they say what happens to a function over the
ceiling that was left alone over the same two years.

Whether behaviour was unchanged is not checked, only suggested by the subject and the
untouched tests. A function renamed or moved to another file is lost from then on, in both
groups alike. Rows are kept in <out dir>/<repo>.json, so a second call only reports.
"""

import collections
import concurrent.futures
import functools
import hashlib
import json
import os
import random
import re
import sys

import lizard

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import later_fixes as fixes   # noqa: E402  the fix rule, git, the production-file rule, the sign test

REFACTOR = re.compile(r"\b(refactor\w*|extract\w*|split\w*|simplif\w*|clean(ed)? ?up|decompos\w*|break up)\b", re.I)
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? ")
CC, LENGTH, DAYS, CONTROLS, PICK, MOVED = 8, 60, 365, 5, 15, 8


@functools.lru_cache(maxsize=4096)
def measured(repo, blob, name):
    """{function name: [(first line, last line, cyclomatic, length)]} of one version of a file."""
    out = {}
    try:
        found = lizard.analyze_file.analyze_source_code(name, fixes.git(repo, "cat-file", "blob", blob)).function_list
    except Exception:   # a file lizard cannot read measures nothing
        return out
    for f in found:
        out.setdefault(f.name, []).append((f.start_line, f.end_line, f.cyclomatic_complexity, f.length))
    return out


def functions(repo, sha, name):
    blob = fixes.git(repo, "rev-parse", "--verify", "-q", "%s:%s" % (sha, name)).strip()
    return measured(repo, blob, name) if blob else {}


def over(spans):
    return len(spans) == 1 and (spans[0][2] > CC or spans[0][3] > LENGTH)


def commits(repo):
    """[(sha, time, subject, {file: (added, deleted)})], newest first."""
    out = []
    for record in fixes.git(repo, "log", "--no-merges", "--no-renames", "--numstat", "--format=%x1e%H%x1f%ct%x1f%s").split("\x1e")[1:]:
        head, _, rest = record.partition("\n")
        sha, when, subject = head.split("\x1f", 2)
        files = {}
        for row in filter(None, rest.splitlines()):
            added, deleted, name = row.split("\t", 2)
            files[name] = (int(added) if added.isdigit() else 0, int(deleted) if deleted.isdigit() else 0)
        out.append((sha, int(when), subject, files))
    return out


def eligible(subject, files):
    """The production files a split could be in, or none where the commit is not a candidate."""
    if fixes.FIX.search(subject):
        return []
    tests = any(fixes.summary._is_test(name) for name in files)
    if tests and not REFACTOR.search(subject):
        return []
    return [name for name, (added, deleted) in files.items() if fixes.production(name) and added >= MOVED and deleted >= MOVED]


def splits(repo, sha, name):
    """[(function, [functions the commit added to the file])] for each function taken under the ceiling."""
    before, after = functions(repo, sha + "^", name), functions(repo, sha, name)
    new = sorted(set(after) - set(before))
    if not new:
        return []
    return [(f, new) for f, spans in before.items() if over(spans) and len(after.get(f, [])) == 1 and not over(after[f])]


def touched(repo, sha, name, names):
    """Whether the commit changes a line inside one of the named functions, as its parent has them."""
    spans = [s for f, found in functions(repo, sha + "^", name).items() if f in names for s in found]
    if not spans:
        return False
    for line in fixes.git(repo, "show", "--format=", "--unified=0", "--no-renames", sha, "--", name).splitlines():
        m = HUNK.match(line)
        if m:
            first, last = int(m.group(1)), int(m.group(1)) + max(int(m.group(2) or 1), 1) - 1
            if any(first <= end and last >= start for start, end, _, _ in spans):
                return True
    return False


def fixed(repo, name, names, since, until):
    """How many fixes in (since, until] touched the named functions of the file."""
    rows = fixes.git(repo, "log", "--no-merges", "--format=%H\t%s", "--since=@%d" % since, "--until=@%d" % until, "--", name).splitlines()
    return sum(touched(repo, sha, name, names) for sha, subject in (row.split("\t", 1) for row in rows) if fixes.FIX.search(subject))


def controls(repo, sha, taken):
    """Up to CONTROLS (file, function) over the ceiling at the commit's parent that it did not split."""
    names = [n for n in fixes.git(repo, "ls-tree", "-r", "--name-only", sha + "^").splitlines() if fixes.production(n)]
    draw = random.Random(int(hashlib.sha1(sha.encode()).hexdigest(), 16))
    draw.shuffle(names)
    found = [(name, f) for name in names[:PICK] for f, spans in sorted(functions(repo, sha + "^", name).items()) if over(spans) and (name, f) not in taken]
    draw.shuffle(found)
    return found[:CONTROLS]


@functools.lru_cache(maxsize=4096)
def at(repo, when):
    """The newest commit at or before a time."""
    return fixes.git(repo, "rev-list", "-1", "--before=@%d" % when, "HEAD").strip()


def lines(repo, sha, name, names):
    return sum(span[3] for f, spans in functions(repo, sha, name).items() if f in names for span in spans)


def row(repo, sha, when, name, function, after_names, kind):
    start, end, together = when - DAYS * 86400, when + DAYS * 86400, set(after_names)
    return dict(repo=os.path.basename(repo), sha=sha, file=name, function=function, kind=kind, added=len(after_names) - 1,
                existed=function in functions(repo, at(repo, start), name),
                lines_before=lines(repo, sha + "^", name, {function}), lines_after=lines(repo, sha, name, together),
                before=fixed(repo, name, {function}, start, when - 1), after=fixed(repo, name, together, when, end),
                alone=fixed(repo, name, {function}, when, end) if len(together) > 1 else None)


def mined(repo):
    log = commits(repo)
    if not log:
        return []
    newest, oldest, rows = log[0][1], log[-1][1], []
    for sha, when, subject, files in log:
        if not oldest + DAYS * 86400 <= when <= newest - DAYS * 86400:
            continue
        found = [(name, f, new) for name in eligible(subject, files) for f, new in splits(repo, sha, name)]
        rows += [row(repo, sha, when, name, f, [f] + new, "split") for name, f, new in found]
        if found:
            rows += [row(repo, sha, when, name, f, [f], "control") for name, f in controls(repo, sha, {(n, f) for n, f, _ in found})]
    return rows


def one(out_dir, repo):
    kept = os.path.join(out_dir, os.path.basename(repo) + ".json")
    if not os.path.isfile(kept):
        rows = mined(repo)
        with open(kept, "w") as handle:
            json.dump(rows, handle, indent=1)
    return os.path.basename(repo), json.load(open(kept))


def signs(rows, key):
    fewer, more = sum(r[key] < r["before"] for r in rows), sum(r[key] > r["before"] for r in rows)
    return "fewer %d, more %d, p %.3f" % (fewer, more, fixes.sign_test(more, fewer))


def line(label, rows):
    """One group: fixes before and after, per 1,000 lines followed, and the function by itself after a split."""
    for r in rows:
        r["alone"] = r["after"] if r["alone"] is None else r["alone"]
    if not rows:
        return "  %-8s none" % label
    before, after, alone = (sum(r[k] for r in rows) for k in ("before", "after", "alone"))
    was, now = sum(r["lines_before"] for r in rows), sum(r["lines_after"] for r in rows)
    return "  %-8s %4d functions | fixes before %3d on %6d lines (%.1f per 1,000), after %3d on %6d lines (%.1f per 1,000; %s) | the function alone after: %3d (%s)" % (
        label, len(rows), before, was, 1000 * before / max(was, 1), after, now, 1000 * after / max(now, 1), signs(rows, "after"), alone, signs(rows, "alone"))


def report(title, rows):
    print("%s: %d split commits in %d repositories" % (title, len({r["sha"] for r in rows if r["kind"] == "split"}), len({r["repo"] for r in rows if r["kind"] == "split"})))
    for kind in ("split", "control"):
        print(line(kind, [r for r in rows if r["kind"] == kind]))


def main(out_dir, *clone_dirs):
    os.makedirs(out_dir, exist_ok=True)
    repos = sorted(os.path.join(d, name) for d in clone_dirs for name in os.listdir(d) if os.path.exists(os.path.join(d, name, ".git")) or os.path.isfile(os.path.join(d, name, "HEAD")))
    everything = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=6) as pool:
        for name, rows in pool.map(functools.partial(one, out_dir), repos):
            everything += rows
            cut = [r for r in rows if r["kind"] == "split"]
            print("%-28s %3d split commits, %3d functions split, %3d controls" % (name, len({r["sha"] for r in cut}), len(cut), len(rows) - len(cut)), flush=True)
    print()
    old = [r for r in everything if r["existed"]]
    most = collections.Counter(r["repo"] for r in old if r["kind"] == "split").most_common(1)
    report("every function", everything)
    report("functions already there %d days before" % DAYS, old)
    if most:
        report("the same without %s, which holds the most" % most[0][0], [r for r in old if r["repo"] != most[0][0]])


if __name__ == "__main__":
    main(*sys.argv[1:])

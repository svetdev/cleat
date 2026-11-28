#!/usr/bin/env python3
"""bugdraw — draw changes that introduced a bug a later fix came with a test for.

  bugdraw.py <clones dir> <work dir> [count] [per repository] [jobs]

gates/later_fixes.py found that a commit the gates refuse is later fixed more often than one
of its size they pass. That is an association. The test of prevention is to have an agent
make the same change again with and without cleat, and judge it by the test that came with
the fix. A task here is a pair of commits in a Python repository:

  the change   a non-merge commit from 2023-01-01 on that changes 1 to 5 production files
               with 10 to 300 added production lines, and passes draw.py's own validation
               (a test of its own that fails at its parent and passes at it);
  the fix      a later commit whose subject says fix, bug, regression, crash or panic, that
               changes 1 to 3 production files and a test file, and whose removed production
               lines are, at least half of them, lines the change wrote (blamed at the
               fix's parent).

The pair is kept when the bug is shown on the change's own tree: with the fix's test files
laid over it at least one test fails, and with the fix's production patch applied as well
(it must apply) that test passes. Those are the bug tests. cleat's gates are then run on the
change as replay/history.py runs them, so each task says whether the gates would have refused
the commit that carried the bug.

Writes <work>/tasks.json in draw.py's shape, each task with a `bug` (the fix, its test files
and the bug tests) and `refused` (the gates that failed), and <work>/tried.jsonl.
run.py runs the tasks; bugscore.py judges each run by the bug tests.
"""

import collections
import concurrent.futures
import datetime
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "replay"))
import draw      # noqa: E402
import history   # noqa: E402
from draw import mine   # noqa: E402

START = datetime.datetime(2023, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
FIX = re.compile(r"\b(fix(es|ed)?|bug|regression|crash|panic)\b", re.I)
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
SEED = 20261122


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, errors="replace").stdout


def removed(repo, sha):
    """{file: [line]} of the production lines the commit removed or rewrote, numbered in its parent."""
    out, name = collections.defaultdict(list), None
    for line in git(repo, "show", "--format=", "--unified=0", "--no-renames", sha).splitlines():
        if line.startswith("--- "):
            name = None if line == "--- /dev/null" else line[6:]
        elif line.startswith("@@") and name and mine.production(name):
            m = HUNK.match(line)
            out[name] += range(int(m.group(1)), int(m.group(1)) + int(m.group(2) or 1) * bool(int(m.group(2) or 1)))
    return out


def wrote_the_bug(repo, fix):
    """The commit that wrote at least half the production lines the fix removed, or None."""
    blamed = collections.Counter()
    for name, numbers in removed(repo, fix).items():
        args = [arg for n in numbers[:400] for arg in ("-L", "%d,%d" % (n, n))]
        for line in git(repo, "blame", "-w", "-l", "-s", *args, fix + "^", "--", name).splitlines():
            blamed[line.split()[0].lstrip("^")] += 1
    if not blamed:
        return None
    sha, lines = blamed.most_common(1)[0]
    return sha if 2 * lines >= sum(blamed.values()) else None


def files_of(repo, sha):
    return [[r[0][0], r[-1], r[-1]] for r in (line.split("\t") for line in git(repo, "diff", "--name-status", "--no-renames", sha + "^", sha).splitlines()) if len(r) > 1]


def a_change(repo, sha):
    """The change as draw.py's candidate, or None where it is not one this study takes."""
    head = git(repo, "show", "-s", "--format=%P%x01%ct%x01%B", sha).split("\x01", 2)
    if len(head) < 3 or len(head[0].split()) != 1 or float(head[1]) < START:
        return None
    files = files_of(repo, sha)
    prod = [f[2] for f in files if mine.production(f[2])]
    added = sum(int(row.split("\t")[0]) for row in git(repo, "diff", "--numstat", "--no-renames", sha + "^", sha, "--", *prod).splitlines()
                if row.split("\t")[0].isdigit()) if prod else 0
    if not (1 <= len(prod) <= 5 and 10 <= added <= 300 and any(draw.is_test(f[2]) for f in files)):
        return None
    return dict(repo=os.path.basename(repo), sha=sha, parent=head[0].strip(), message=head[2].strip(), files=files)


def candidates(clones):
    """One (change, fix) per change: the earliest fix that landed on it."""
    out = {}
    for name in sorted(os.listdir(clones)):
        repo = os.path.join(clones, name)
        rows = git(repo, "log", "--no-merges", "--reverse", "--since=2023-01-01", "--format=%H\t%s", "HEAD").splitlines()
        for row in rows:
            fix, _, subject = row.partition("\t")
            if not FIX.search(subject):
                continue
            files = files_of(repo, fix)
            prod = [f[2] for f in files if mine.production(f[2])]
            if not (1 <= len(prod) <= 3 and any(draw.is_test(f[2]) for f in files)):
                continue
            sha = wrote_the_bug(repo, fix)
            if sha and sha not in out:
                cand = a_change(repo, sha)
                if cand:
                    out[sha] = dict(cand, bug=dict(sha=fix, subject=subject, files=files))
        print(name, sum(1 for c in out.values() if c["repo"] == name), flush=True)
    return list(out.values())


def bug_tests(clones, work, cand, recipe):
    """The fix's tests that fail on the change's tree and pass once the fix's production patch is on it."""
    clone, fix = os.path.join(clones, cand["repo"]), cand["bug"]
    root = os.path.join(work, "validate", "bug-" + cand["repo"] + "-" + cand["sha"][:8])
    draw.tree(clone, cand["sha"], root)
    try:
        draw.install_recipe(root, recipe)
        others = [f[2] for f in fix["files"] if not mine.production(f[2])]
        prods = [f[2] for f in fix["files"] if mine.production(f[2])]
        tests = [p for s, _, p in fix["files"] if s != "D" and draw.is_test(p) and (os.path.basename(p).startswith("test") or p.endswith("_test.py"))]
        if not tests:
            return None, "the fix has no runnable test file"
        draw.overlay(clone, fix["sha"], root, others)
        before = draw.pytest(root, tests, "bug-before")
        patch = subprocess.run(["git", "-C", clone, "diff", fix["sha"] + "^", fix["sha"], "--", *prods], capture_output=True)
        if subprocess.run(["git", "apply", "-"], input=patch.stdout, cwd=root, capture_output=True).returncode:
            return None, "the fix does not apply to the change's tree"
        after = draw.pytest(root, tests, "bug-after")
        f2p = sorted(t for t, ok in after.items() if ok and not before.get(t, False))
        if not f2p:
            return None, "no test shows the bug on the change's tree"
        return dict(overlay=others, tests=tests, f2p=f2p, p2p=sorted(t for t, ok in after.items() if ok and before.get(t, False))), "ok"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def validate(clones, work, cand):
    try:
        own, why = draw.validate(clones, work, cand)
        if not own:
            return None, "the change: " + why
        bug, why = bug_tests(clones, work, cand, own["recipe"])
        if not bug:
            return None, why
    except subprocess.TimeoutExpired:
        return None, "timeout"
    verdict = history.one(os.path.join(clones, cand["repo"]), os.path.join(work, "gates"), cand["sha"], [[f[0], f[2]] for f in cand["files"]])
    text, source = draw.task_text(cand)
    return dict(cand, **own, bug=dict(cand["bug"], **bug), refused=verdict.get("failing", []), said=verdict.get("said", ""),
                text=text, source=source, id="%s-%s" % (cand["repo"], cand["sha"][:8])), "ok"


def main(clones, work, count=40, per_repo=5, jobs=4):
    clones, work, count, per_repo = os.path.abspath(clones), os.path.abspath(work), int(count), int(per_repo)
    os.makedirs(work, exist_ok=True)
    pool = candidates(clones)
    random.Random(SEED).shuffle(pool)
    kept, taken, lock = [], collections.Counter(), threading.Lock()
    log = open(os.path.join(work, "tried.jsonl"), "w")

    def one(cand):
        with lock:
            if len(kept) >= count or taken[cand["repo"]] >= per_repo:
                return
        task, why = validate(clones, work, cand)
        with lock:
            log.write(json.dumps(dict(repo=cand["repo"], sha=cand["sha"], fix=cand["bug"]["sha"], outcome=why)) + "\n")
            log.flush()
            print(cand["repo"], cand["sha"][:8], why, task and task["refused"], flush=True)
            if task and len(kept) < count and taken[cand["repo"]] < per_repo:
                kept.append(task)
                taken[cand["repo"]] += 1
                with open(os.path.join(work, "tasks.json"), "w") as handle:
                    json.dump(dict(pool=len(pool), seed=SEED, tasks=kept), handle, indent=1)

    with concurrent.futures.ThreadPoolExecutor(int(jobs)) as workers:
        list(workers.map(one, pool))
    print("pool %d, kept %d, of them refused %d" % (len(pool), len(kept), sum(bool(t["refused"]) for t in kept)))


if __name__ == "__main__":
    main(*sys.argv[1:])

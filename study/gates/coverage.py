#!/usr/bin/env python3
"""coverage — does low coverage of the lines an agent changed mark a wrong solution?

  coverage.py <out dir> <coverage package dir> <paired work dir> [<paired work dir> …]

Reads runs that paired/run.py already made; starts no agent. Per run: the production lines
the agent changed, the task's test files and the tests the agent itself changed run under
coverage in the run's own tree, the share of the changed executable lines that ran, and the
CRAP (cc² × (1 − coverage)³ + cc) of each function the change touched. Then, against the hidden
tests' verdict: how often a run under 80% changed-line coverage was wrong against the rest,
the same for a touched function with CRAP over 30, and, within the tasks that have both
right and wrong runs, which kind had the higher coverage. Python only.

The coverage package is not in the trees and is not installed into them: <coverage package
dir> is a `pip install --target` of `coverage`, put on PYTHONPATH. One row per run is kept in
<out dir>, so a second call only reads them.
"""

import collections
import concurrent.futures
import glob
import json
import os
import re
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "quality", "bin"))
from extractors import complexity   # noqa: E402

TEST = re.compile(r"(^|/)(tests?|t|testing)/|(^|/)test_|_test\.py$|conftest")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
MINIMUM, CRAP_CEILING = 0.8, 30


def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, errors="replace").stdout


def changed(root):
    """{file: the line numbers the agent's uncommitted change wrote}, new files whole."""
    out, name = {}, None
    for line in git(root, "diff", "HEAD", "--unified=0", "--no-color").splitlines():
        if line.startswith("+++ "):
            name = None if line.endswith("/dev/null") else line[6:]
        elif line.startswith("@@") and name:
            m = HUNK.match(line)
            out.setdefault(name, set()).update(range(int(m.group(1)), int(m.group(1)) + int(m.group(2) or 1)))
    for name in git(root, "ls-files", "--others", "--exclude-standard").splitlines():
        if name.endswith(".py") and not name.startswith(("quality/", ".venv/", ".claude/")):
            out[name] = set(range(1, len(open(os.path.join(root, name), errors="replace").read().split("\n")) + 1))
    return out


def covered(root, tests, production, package, scratch):
    """{file: (lines that ran, lines that did not)} for the production files, from one test run."""
    data, report = scratch + ".cov", scratch + ".cov.json"
    env = dict(os.environ, PYTHONPATH=package, COVERAGE_FILE=data)
    python = os.path.join(root, ".venv", "bin", "python")
    try:
        subprocess.run([python, "-m", "coverage", "run", "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
                       cwd=root, env=env, capture_output=True, text=True, timeout=600)
        subprocess.run([python, "-m", "coverage", "json", "-o", report, "--include", ",".join(production)],
                       cwd=root, env=env, capture_output=True, text=True, timeout=300)
        files = json.load(open(report))["files"] if os.path.isfile(report) else {}
    finally:
        for path in (data, report):
            if os.path.isfile(path):
                os.remove(path)
    return {f: (set(c["executed_lines"]), set(c["missing_lines"])) for f, c in files.items() if f in production}


def crap_of(root, production, per):
    """[(CRAP, cc, coverage)] of each function the change touched."""
    functions, _, _, _ = complexity.measure({"languages": ["python"]}, [os.path.join(root, f) for f in per], root=root)
    out = []
    for fn in functions:
        rel = os.path.relpath(os.path.realpath(fn.path), os.path.realpath(root))
        span = set(range(fn.line, (fn.end or fn.line) + 1))
        if rel in production and span & production[rel]:
            ran, missed = per[rel]
            executable = len(span & (ran | missed))
            share = len(span & ran) / executable if executable else 1.0
            out.append((fn.cc ** 2 * (1 - share) ** 3 + fn.cc, fn.cc, share))
    return out


def one(job, out_dir, package):
    study, task, arm, directory = job
    key = "%s__%s__%s" % (os.path.basename(study), task["id"], arm)
    kept = os.path.join(out_dir, key + ".json")
    if os.path.isfile(kept):
        return json.load(open(kept))
    root = os.path.join(directory, "repo")
    score = json.load(open(os.path.join(directory, "score.json"))).get("score", {})
    row = dict(study=os.path.basename(study), task=task["id"], arm=arm, resolved=score.get("resolved"))
    try:
        lines = changed(root)
        production = {f: l for f, l in lines.items() if f.endswith(".py") and not TEST.search(f)}
        tests = sorted({t for t in task.get("tests", []) if os.path.isfile(os.path.join(root, t))}
                       | {f for f in lines if f.endswith(".py") and TEST.search(f) and "conftest" not in f and os.path.isfile(os.path.join(root, f))})
        if production and tests:
            per = covered(root, tests, production, package, os.path.join(out_dir, key))
            row["changed_exec"] = sum(len(l & (per[f][0] | per[f][1])) for f, l in production.items() if f in per)
            row["changed_covered"] = sum(len(l & per[f][0]) for f, l in production.items() if f in per)
            row["max_crap"] = max((c[0] for c in crap_of(root, production, per)), default=None)
    except Exception as problem:   # one broken tree must not lose the other runs' rows
        row["error"] = repr(problem)[:200]
    with open(kept, "w") as handle:
        json.dump(row, handle)
    return row


def split(rows, flagged, label):
    hit, rest = [r for r in rows if flagged(r)], [r for r in rows if not flagged(r)]
    wrong = lambda group: sum(not r["resolved"] for r in group)   # noqa: E731
    print("%-42s flagged %3d runs, wrong %3d (%2.0f%%) | not flagged %3d, wrong %3d (%2.0f%%)" % (
        label, len(hit), wrong(hit), 100 * wrong(hit) / max(len(hit), 1), len(rest), wrong(rest), 100 * wrong(rest) / max(len(rest), 1)))


def report(rows):
    rows = [r for r in rows if r.get("changed_exec")]
    share = lambda r: r["changed_covered"] / r["changed_exec"]   # noqa: E731
    print("%d runs measured, %d right and %d wrong by the hidden tests" % (
        len(rows), sum(bool(r["resolved"]) for r in rows), sum(not r["resolved"] for r in rows)))
    split(rows, lambda r: share(r) < MINIMUM, "changed-line coverage under %d%%" % (100 * MINIMUM))
    split(rows, lambda r: share(r) < MINIMUM and r["changed_exec"] >= 20, "  the same, 20 changed lines or more")
    split(rows, lambda r: (r.get("max_crap") or 0) > CRAP_CEILING, "a touched function with CRAP over %d" % CRAP_CEILING)
    by_task = collections.defaultdict(list)
    for r in rows:
        by_task[(r["study"], r["task"])].append(r)
    gaps = [statistics.mean(share(r) for r in rs if r["resolved"]) - statistics.mean(share(r) for r in rs if not r["resolved"])
            for rs in by_task.values() if 0 < sum(bool(r["resolved"]) for r in rs) < len(rs)]
    print("tasks with right and wrong runs: %d; right runs' coverage higher in %d, lower in %d, mean gap %+.2f" % (
        len(gaps), sum(g > 0 for g in gaps), sum(g < 0 for g in gaps), statistics.mean(gaps) if gaps else 0))


def main(out_dir, package, *studies):
    os.makedirs(out_dir, exist_ok=True)
    out_dir, package = os.path.abspath(out_dir), os.path.abspath(os.path.expanduser(package))
    jobs = []
    for study in studies:
        for task in json.load(open(os.path.join(study, "tasks.json")))["tasks"]:
            for directory in sorted(glob.glob(os.path.join(study, "runs", task["id"], "*"))):
                if os.path.isfile(os.path.join(directory, "score.json")) and os.path.isfile(os.path.join(directory, "repo", ".venv", "bin", "python")):
                    jobs.append((os.path.normpath(study), task, os.path.basename(directory), directory))
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        report(list(pool.map(lambda job: one(job, out_dir, package), jobs)))


if __name__ == "__main__":
    main(*sys.argv[1:])

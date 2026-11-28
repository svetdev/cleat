#!/usr/bin/env python3
"""own_coverage — coverage.py's question again, with the agent's own tests in place.

  own_coverage.py <out dir> <coverage package dir> <paired work dir> [<paired work dir> …]

coverage.py read each run's tree as scoring left it: score.py had laid the commit's hidden
test files over the task's test files, so the coverage it called the run's own was the hidden
tests'. This measures every run twice, in the same tree and by the same steps: once as the
tree stands (hidden tests, what coverage.py read) and once with the task's test files put
back as the agent left them, rebuilt from the transcript (paired/replayed.py), and then
restored. A run whose other files do not rebuild to what is on disk is not measured, and a
run whose tests do not run (pytest exit over 1) is not counted. Starts no agent.

A function's coverage is read as the fixed check-crap.py reads it: the lines of its body, the
declaration line left out, since that line runs at import whether or not anything calls it.

One row per run is kept in <out dir>, so a second call only reports: for hidden and for own
tests, how often a run under 80% changed-line coverage was wrong against the rest, the same
for a touched function with CRAP over 30, and the within-task comparison.
"""

import collections
import concurrent.futures
import glob
import importlib.util
import json
import os
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("coverage_study", os.path.join(HERE, "coverage.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "paired"))
import replayed   # noqa: E402

BAD_SUITES = ("Textualize__textual", "scrapy__scrapy", "celery__celery", "encode__httpx", "mitmproxy__mitmproxy")


def covered(root, tests, production, package, scratch):
    """(pytest's exit, {file: (lines that ran, lines that did not)}) from one run of the tests."""
    data, report = scratch + ".cov", scratch + ".cov.json"
    env = dict(os.environ, PYTHONPATH=package, COVERAGE_FILE=data, PYTHONDONTWRITEBYTECODE="1")
    python = os.path.join(root, ".venv", "bin", "python")
    try:
        ran = subprocess.run([python, "-B", "-m", "coverage", "run", "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
                             cwd=root, env=env, capture_output=True, text=True, timeout=600)
        subprocess.run([python, "-B", "-m", "coverage", "json", "-o", report, "--include", ",".join(production)],
                       cwd=root, env=env, capture_output=True, text=True, timeout=300)
        files = json.load(open(report))["files"] if os.path.isfile(report) else {}
    finally:
        for path in (data, report):
            if os.path.isfile(path):
                os.remove(path)
    return ran.returncode, {f: (set(c["executed_lines"]), set(c["missing_lines"])) for f, c in files.items() if f in production}


def crap_of(root, production, per):
    """[(CRAP, cc, coverage)] of each function the change touched, coverage over the body's lines."""
    functions, _, _, _ = base.complexity.measure({"languages": ["python"]}, [os.path.join(root, f) for f in per], root=root)
    out = []
    for fn in functions:
        rel = os.path.relpath(os.path.realpath(fn.path), os.path.realpath(root))
        if rel in production and set(range(fn.line, (fn.end or fn.line) + 1)) & production[rel]:
            ran, missed = per[rel]
            body = set(range(fn.line + 1, (fn.end or fn.line) + 1)) & (ran | missed) or {fn.line} & (ran | missed)
            share = len(body & ran) / len(body) if body else 1.0
            out.append((fn.cc ** 2 * (1 - share) ** 3 + fn.cc, fn.cc, share))
    return out


def measured(task, root, package, scratch):
    """The run's numbers with the tree as it stands."""
    lines = base.changed(root)
    production = {f: l for f, l in lines.items() if f.endswith(".py") and not base.TEST.search(f)}
    tests = sorted({t for t in task.get("tests", []) if os.path.isfile(os.path.join(root, t))}
                   | {f for f in lines if f.endswith(".py") and base.TEST.search(f) and "conftest" not in f and os.path.isfile(os.path.join(root, f))})
    if not production or not tests:
        return dict(skipped="no changed production line" if not production else "no test file")
    code, per = covered(root, tests, production, package, scratch)
    craps = crap_of(root, production, per)
    return dict(exit=code, tests=len(tests),
                changed_exec=sum(len(l & (per[f][0] | per[f][1])) for f, l in production.items() if f in per),
                changed_covered=sum(len(l & per[f][0]) for f, l in production.items() if f in per),
                max_crap=max((c[0] for c in craps), default=None),
                unpaid8=any(c[1] ** 2 * (1 - c[2]) ** 3 + min(c[1], 8) > 8 for c in craps))


def swapped(root, texts):
    """Write {path: text or None} into the tree; returns what was there, to swap back."""
    before = {}
    for path, text in texts.items():
        full = os.path.join(root, path)
        before[path] = open(full, errors="replace").read() if os.path.isfile(full) else None
        if text is None:
            if os.path.isfile(full):
                os.remove(full)
        else:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as handle:
                handle.write(text)
    return before


def one(job, out_dir, package):
    study, task, arm, directory = job
    key = "%s__%s__%s" % (os.path.basename(study), task["id"], arm)
    kept = os.path.join(out_dir, key + ".json")
    if os.path.isfile(kept):
        return json.load(open(kept))
    root, transcript = os.path.join(directory, "repo"), os.path.join(directory, "transcript.jsonl")
    score = json.load(open(os.path.join(directory, "score.json"))).get("score", {})
    row = dict(study=os.path.basename(study), task=task["id"], repo=task["repo"], arm=arm, resolved=score.get("resolved"))
    try:
        row["hidden"] = measured(task, root, package, os.path.join(out_dir, key + ".hidden"))
        if not os.path.isfile(transcript) or not replayed.faithful(root, transcript, set(task["overlay"])):
            row["unreplayable"] = True
        else:
            mine = replayed.rebuilt(root, transcript, task["overlay"])
            row["tests_differ"] = any(text != (open(os.path.join(root, p), errors="replace").read() if os.path.isfile(os.path.join(root, p)) else None)
                                      for p, text in mine.items())
            hidden = swapped(root, mine)
            try:
                row["own"] = measured(task, root, package, os.path.join(out_dir, key + ".own"))
            finally:
                swapped(root, hidden)
    except Exception as problem:   # one broken tree must not lose the other runs' rows
        row["error"] = repr(problem)[:200]
    with open(kept, "w") as handle:
        json.dump(row, handle)
    return row


def report(rows, which, label):
    flat = [dict(r[which], study=r["study"], task=r["task"], resolved=r["resolved"]) for r in rows]
    flat = [r for r in flat if r.get("changed_exec")]
    share = lambda r: r["changed_covered"] / r["changed_exec"]   # noqa: E731
    print("\n%s: %d runs, %d right and %d wrong by the hidden tests" % (
        label, len(flat), sum(bool(r["resolved"]) for r in flat), sum(not r["resolved"] for r in flat)))
    base.split(flat, lambda r: share(r) < base.MINIMUM, "changed-line coverage under %d%%" % (100 * base.MINIMUM))
    base.split(flat, lambda r: share(r) < base.MINIMUM and r["changed_exec"] >= 20, "  the same, 20 changed lines or more")
    base.split(flat, lambda r: (r.get("max_crap") or 0) > base.CRAP_CEILING, "a touched function with CRAP over %d" % base.CRAP_CEILING)
    base.split(flat, lambda r: r.get("unpaid8"), "a touched function refused by CRAP at 8, paths capped")
    by_task = collections.defaultdict(list)
    for r in flat:
        by_task[(r["study"], r["task"])].append(r)
    gaps = [statistics.mean(share(r) for r in rs if r["resolved"]) - statistics.mean(share(r) for r in rs if not r["resolved"])
            for rs in by_task.values() if 0 < sum(bool(r["resolved"]) for r in rs) < len(rs)]
    print("tasks with right and wrong runs: %d; right runs' coverage higher in %d, lower in %d, mean gap %+.2f" % (
        len(gaps), sum(g > 0 for g in gaps), sum(g < 0 for g in gaps), statistics.mean(gaps) if gaps else 0))


def summary(rows):
    usable = lambda r, which: which in r and r[which].get("exit", 9) <= 1 and r[which].get("changed_exec")   # noqa: E731
    both = [r for r in rows if usable(r, "hidden") and usable(r, "own")]
    print("%d runs; errors %d; not replayable %d; no changed production line or no test file %d; tests did not run (exit over 1): hidden %d, own %d" % (
        len(rows), sum("error" in r for r in rows), sum(bool(r.get("unreplayable")) for r in rows),
        sum("skipped" in r.get("hidden", {}) or "skipped" in r.get("own", {}) for r in rows),
        sum(r.get("hidden", {}).get("exit", 0) > 1 for r in rows), sum(r.get("own", {}).get("exit", 0) > 1 for r in rows)))
    print("measured both ways: %d runs; the agent's version of the task's test files differs from the hidden one in %d" % (
        len(both), sum(bool(r.get("tests_differ")) for r in both)))
    for label, group in (("all repositories", both), ("without %s" % ", ".join(n.split("__")[1] for n in BAD_SUITES), [r for r in both if r["repo"] not in BAD_SUITES])):
        print("\n==== %s" % label)
        report(group, "hidden", "hidden tests in place (what coverage.py read)")
        report(group, "own", "the agent's own tests")


def main(out_dir, package, *studies):
    os.makedirs(out_dir, exist_ok=True)
    out_dir, package = os.path.abspath(out_dir), os.path.abspath(os.path.expanduser(package))
    jobs = []
    for study in map(os.path.abspath, studies):
        for task in json.load(open(os.path.join(study, "tasks.json")))["tasks"]:
            for directory in sorted(glob.glob(os.path.join(study, "runs", task["id"], "*"))):
                if os.path.isfile(os.path.join(directory, "score.json")) and os.path.isfile(os.path.join(directory, "repo", ".venv", "bin", "python")):
                    jobs.append((study, task, os.path.basename(directory), directory))
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        summary(list(pool.map(lambda job: one(job, out_dir, package), jobs)))


if __name__ == "__main__":
    main(*sys.argv[1:])

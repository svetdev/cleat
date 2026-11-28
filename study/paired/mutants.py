#!/usr/bin/env python3
"""mutants — do the tests in each run's tree catch a bug planted in the lines the agent wrote?

  mutants.py <work dir> [mutants per run] [jobs] [arm …]

After run.py. Starts no agent. Per run: the production lines the agent changed are read for
places one token can be flipped (a comparison, `==`/`!=`, `and`/`or`, `True`/`False`, a dropped
`not`); up to <mutants per run> are drawn with a seed from the task and the run. The tests are
the agent's own: the task's test files as the agent left them and every other test file it
changed. score.py lays the commit's hidden tests over the task's test files, so the agent's
version is taken from run.py's copy in <run>/agent, or where a run has none rebuilt from the
transcript (replayed.py), and put in place for the mutants, then the
hidden ones are put back. A run whose other files do not rebuild to what is on disk, because
the agent wrote through a script, is not scored. Each mutant is written into the file, the tests run, and the file is put
back. A mutant is killed when a test that passed on the agent's own code fails, stops being
reported, or the run passes TIMES the clean run's time. One that does not compile is left out.
A run with no such place, or no passing test, has no score.

Written beside the run's score.json as mutants.json, so a second call only reports: per arm,
mutants killed of those tried, pooled and as the mean of the tasks' rates, and the tasks where
the arms differ.
"""

import collections
import concurrent.futures
import glob
import hashlib
import importlib.util
import io
import json
import os
import random
import statistics
import subprocess
import sys
import time
import tokenize
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("coverage_study", os.path.join(os.path.dirname(HERE), "gates", "coverage.py"))
lines_of = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lines_of)
sys.path.insert(0, HERE)
import replayed   # noqa: E402

FLIPS = {"==": "!=", "!=": "==", "<": "<=", "<=": "<", ">": ">=", ">=": ">", "and": "or", "or": "and", "True": "False", "False": "True"}
TIMES, FLOOR = 4, 60


def sites(source, wanted):
    """[(row, column, old, new)] for the flippable tokens on the wanted lines."""
    out, previous = [], None
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return out
    for index, token in enumerate(tokens):
        if token.type in (tokenize.OP, tokenize.NAME) and token.start[0] in wanted:
            following = tokens[index + 1].string if index + 1 < len(tokens) else ""
            if token.string in FLIPS:
                out.append((token.start[0], token.start[1], token.string, FLIPS[token.string]))
            elif token.string == "not" and token.type == tokenize.NAME and following != "in" and previous != "is":
                out.append((token.start[0], token.start[1], "not ", ""))
        previous = token.string
    return out


def flipped(source, site):
    row, column, old, new = site
    rows = source.split("\n")
    line = rows[row - 1]
    if not line[column:].startswith(old.strip()):
        return None
    rows[row - 1] = line[:column] + new + line[column + len(old):] if line[column:].startswith(old) else line[:column] + new + line[column + len(old.strip()):]
    return "\n".join(rows)


def tests_run(root, tests, label, limit):
    """({test id: passed}, seconds), or (None, limit) where the run passed the limit."""
    report = os.path.join(root, ".junit-%s.xml" % label)
    start = time.time()
    try:
        subprocess.run([os.path.join(root, ".venv", "bin", "python"), "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q", "-x",
                        "--junitxml", report, *tests], cwd=root, capture_output=True, timeout=limit,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    except subprocess.TimeoutExpired:
        return None, limit
    out = {}
    if os.path.isfile(report):
        for case in ET.parse(report).getroot().iter("testcase"):
            if not any(child.tag == "skipped" for child in case):
                out["%s::%s" % (case.get("classname"), case.get("name"))] = not any(child.tag in ("failure", "error") for child in case)
        os.remove(report)
    return out, time.time() - start


def judge(root, tests, name, source, site, passing, limit):
    changed = flipped(source, site)
    if changed is None:
        return "invalid"
    try:
        compile(changed, name, "exec")
    except SyntaxError:
        return "invalid"
    path = os.path.join(root, name)
    try:
        with open(path, "w") as handle:
            handle.write(changed)
        outcome, _ = tests_run(root, tests, "mutant", limit)
    finally:
        with open(path, "w") as handle:
            handle.write(source)
    if outcome is None:
        return "killed"
    return "survived" if all(outcome.get(test) for test in passing) else "killed"


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


def kept_tests(root, saved, paths):
    """{path: text or None}: each test file as the agent left it, from run.py's copy, or as the commit had it where the agent left it alone."""
    out = {}
    for path in paths:
        mine = os.path.join(saved, path)
        if os.path.isfile(mine):
            out[path] = open(mine, errors="replace").read()
        else:
            shown = subprocess.run(["git", "-C", root, "show", "HEAD:" + path], capture_output=True, text=True)
            out[path] = shown.stdout if shown.returncode == 0 else None
    return out


def one(task, directory, count):
    kept = os.path.join(directory, "mutants.json")
    if os.path.isfile(kept):
        return json.load(open(kept))
    root, arm, transcript = os.path.join(directory, "repo"), os.path.basename(directory), os.path.join(directory, "transcript.jsonl")
    row = dict(task=task["id"], arm=arm, sites=0, tried=0, killed=0, invalid=0, own_tests=0)
    saved = os.path.join(directory, "agent")
    if os.path.isdir(saved) or replayed.faithful(root, transcript, set(task["overlay"])):
        hidden = swapped(root, kept_tests(root, saved, task["overlay"]) if os.path.isdir(saved) else replayed.rebuilt(root, transcript, task["overlay"]))
        try:
            measure(task, root, arm, count, row)
        finally:
            swapped(root, hidden)
    else:
        row["unreplayable"] = True
    with open(kept, "w") as handle:
        json.dump(row, handle)
    return row


def measure(task, root, arm, count, row):
    lines = lines_of.changed(root)
    production = {f: l for f, l in lines.items() if f.endswith(".py") and not lines_of.TEST.search(f)}
    tests = sorted({t for t in task.get("tests", []) if os.path.isfile(os.path.join(root, t))}
                   | {f for f in lines if f.endswith(".py") and lines_of.TEST.search(f) and "conftest" not in f and os.path.isfile(os.path.join(root, f))})
    row["own_tests"] = sum(1 for f in lines if f.endswith(".py") and lines_of.TEST.search(f))
    row["test_lines"] = sum(len(l) for f, l in lines.items() if f.endswith(".py") and lines_of.TEST.search(f))
    sources = {f: open(os.path.join(root, f), errors="replace").read() for f in production if os.path.isfile(os.path.join(root, f))}
    places = [(f, site) for f in sorted(sources) for site in sites(sources[f], production[f])]
    row["sites"] = len(places)
    if places and tests:
        clean, seconds = tests_run(root, tests, "clean", 900)
        passing = [test for test, ok in (clean or {}).items() if ok]
        row["passing"] = len(passing)
        if passing:
            limit = max(FLOOR, TIMES * seconds)
            random.Random(int(hashlib.sha1((task["id"] + arm).encode()).hexdigest(), 16)).shuffle(places)
            for name, site in places[:count]:
                verdict = judge(root, tests, name, sources[name], site, passing, limit)
                row["invalid" if verdict == "invalid" else "tried"] += 1
                row["killed"] += verdict == "killed"


def report(rows, arms):
    by_task = collections.defaultdict(dict)
    for arm in arms:
        mine = [r for r in rows if r["arm"].split("-")[0] == arm]
        scored = [r for r in mine if r["tried"]]
        tasks = collections.defaultdict(lambda: [0, 0])
        for r in scored:
            tasks[r["task"]][0] += r["killed"]
            tasks[r["task"]][1] += r["tried"]
        for task, (killed, tried) in tasks.items():
            by_task[task][arm] = killed / tried
        tried = sum(r["tried"] for r in scored)
        print("%-8s %3d runs, %3d not replayable, %3d scored, %3d changed a test, median %d test lines | killed %4d of %4d (%.1f%%) | mean of %d tasks' rates %.1f%%" % (
            arm, len(mine), sum(bool(r.get("unreplayable")) for r in mine), len(scored), sum(bool(r["own_tests"]) for r in mine),
            statistics.median([r.get("test_lines", 0) for r in mine if not r.get("unreplayable")] or [0]), sum(r["killed"] for r in scored), tried,
            100 * sum(r["killed"] for r in scored) / max(tried, 1), len(tasks), 100 * statistics.mean(by_task[t][arm] for t in tasks) if tasks else 0))
    both = {t: v for t, v in by_task.items() if all(a in v for a in arms)}
    for left, right in ((arms[0], arm) for arm in arms[1:]):
        gaps = [v[right] - v[left] for v in both.values()]
        print("%s against %s, %d tasks scored in both: higher in %d, lower in %d, mean gap %+.1f points" % (
            right, left, len(gaps), sum(g > 0 for g in gaps), sum(g < 0 for g in gaps), 100 * statistics.mean(gaps) if gaps else 0))


def main(work, count="12", jobs="6", *arms):
    arms = arms or ("cleat", "crap")
    tasks = json.load(open(os.path.join(work, "tasks.json")))["tasks"]
    jobs_list = [(task, directory) for task in tasks for directory in sorted(glob.glob(os.path.join(work, "runs", task["id"], "*")))
                 if os.path.isfile(os.path.join(directory, "score.json")) and os.path.basename(directory).split("-")[0] in arms]
    with concurrent.futures.ThreadPoolExecutor(int(jobs)) as pool:
        rows = list(pool.map(lambda job: one(job[0], job[1], int(count)), jobs_list))
    report(rows, arms)


if __name__ == "__main__":
    main(*sys.argv[1:])

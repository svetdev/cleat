#!/usr/bin/env python3
"""crapdraw — the tasks for the CRAP arm: earlier Python tasks on which CRAP can be a gate at all.

  crapdraw.py <clones dir> <work dir> <earlier paired work dir> [<earlier paired work dir> …]

Starts no agent. Every Python task of the earlier draws that has a run on disk is prepared as
run.py prepares a STUDY_CRAP arm, and is usable when the whole suite runs under coverage
(pytest exits 0 or 1, SHARE of its tests passing), the CRAP baseline is written, and the Stop
hook's gates pass on the untouched tree within LIMIT seconds. A repository whose first two
tasks are both unusable is not tried again. Kept: every usable task that earlier runs did
not always resolve, which is where a gate could make an agent more correct, and then usable
tasks every run resolved, in seeded order, up to COUNT in all and PER_REPO per repository.
Writes <work>/tasks.json in draw.py's shape, each task with `earlier` (resolved, runs), and
<work>/tried.jsonl with why each was usable or not.
"""

import collections
import concurrent.futures
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time

os.environ.setdefault("STUDY_ARMS", "cleat,crap")
os.environ.setdefault("STUDY_CRAP", "crap")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run   # noqa: E402

LIMIT = 300
SHARE = 0.9
COUNT, PER_REPO, SEED = 24, 6, 20261123


def earlier(works):
    """{task id: (task, resolved, runs)} for the Python tasks with a run on disk."""
    tasks, seen = {}, {}
    for work in works:
        for task in json.load(open(os.path.join(work, "tasks.json")))["tasks"]:
            if not any(name.endswith(".py") for name in task.get("tests", [])):
                continue
            tasks.setdefault(task["id"], task)
            for path in glob.glob(os.path.join(work, "runs", task["id"], "*", "score.json")):
                seen.setdefault(task["id"], []).append(bool(json.load(open(path))["score"].get("resolved")))
    return {i: (tasks[i], sum(r), len(r)) for i, r in seen.items()}


def check(clones, work, task):
    root = os.path.join(work, "check", task["id"], "repo")
    shutil.rmtree(os.path.dirname(root), ignore_errors=True)
    os.makedirs(os.path.dirname(root))
    try:
        info = run.prepare(clones, task, "crap", root)
        if info["crap"].get("baseline") != 0:
            return "no baseline: %s" % (info["crap"].get("said") or info["crap"]["baseline"]), None
        suite = info["crap"]["suite"]
        if suite["exit"] > 1:
            return "the suite does not run (pytest exit %d): %s" % (suite["exit"], suite["said"]), None
        counts = {word.rstrip("s"): int(n) for n, word in re.findall(r"(\d+) (passed|failed|errors?)", suite["said"])}
        if counts.get("passed", 0) < SHARE * sum(counts.values()):
            return "under %d%% of the suite passes: %s" % (100 * SHARE, suite["said"]), None
        if info["day_one_gate"] != 0:
            return "attach is red on day one", None
        start = time.time()
        gate = subprocess.run(["python3", "quality/bin/gate.py", "--hook", "--changed"], cwd=root, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, env=run.clean_env(), timeout=LIMIT)
        if gate.returncode:
            return "the gates fail on the untouched tree: " + (gate.stdout + gate.stderr)[-200:], None
        return "ok", round(time.time() - start, 1)
    except subprocess.TimeoutExpired:
        return "the gates take over %ds" % LIMIT, None
    finally:
        shutil.rmtree(os.path.dirname(root), ignore_errors=True)


def repository(clones, work, items, log, lock):
    usable, failed = [], 0
    for name, (task, resolved, runs) in items:
        if failed >= 2 and not usable:
            why, seconds = "not tried: the repository's first two tasks were unusable", None
        else:
            why, seconds = check(clones, work, task)
            failed += not why.startswith("ok")
        with lock:
            log.write(json.dumps(dict(id=name, outcome=why, gate_seconds=seconds, earlier="%d/%d" % (resolved, runs))) + "\n")
            log.flush()
            print(name, "%d/%d" % (resolved, runs), why[:150], seconds, flush=True)
        if why.startswith("ok"):
            usable.append(dict(task, earlier=[resolved, runs], gate_seconds=seconds))
    return usable


def main(clones, work, *works):
    clones, work = os.path.abspath(clones), os.path.abspath(work)
    os.makedirs(work, exist_ok=True)
    pool, repos = earlier(works), {}
    for name, item in sorted(pool.items()):
        repos.setdefault(item[0]["repo"], []).append((name, item))
    lock = threading.Lock()
    with concurrent.futures.ThreadPoolExecutor(4) as workers, open(os.path.join(work, "tried.jsonl"), "w") as log:
        usable = [t for part in workers.map(lambda items: repository(clones, work, items, log, lock), repos.values()) for t in part]
    hard = [t for t in usable if t["earlier"][0] < t["earlier"][1]]
    easy = [t for t in usable if t["earlier"][0] == t["earlier"][1]]
    random.Random(SEED).shuffle(easy)
    kept, taken = list(hard), collections.Counter(t["repo"] for t in hard)
    for task in easy:
        if len(kept) < COUNT and taken[task["repo"]] < PER_REPO:
            kept.append(task)
            taken[task["repo"]] += 1
    with open(os.path.join(work, "tasks.json"), "w") as handle:
        json.dump(dict(pool=len(pool), usable=len(usable), seed=SEED, tasks=kept), handle, indent=1)
    print("pool %d, usable %d, kept %d: %d not always resolved before, %d always" % (len(pool), len(usable), len(kept), len(hard), len(kept) - len(hard)))


if __name__ == "__main__":
    main(*sys.argv[1:])

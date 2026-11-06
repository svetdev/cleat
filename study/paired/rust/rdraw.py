#!/usr/bin/env python3
"""rdraw — the paired-run tasks for Rust, by the Python rule as far as Rust allows.

  rdraw.py <clones dir> <work dir> [count] [per repository] [earlier work dir] [used work dir]

A candidate is a non-merge commit from 2024-01-01 to 2025-06-30 that changes 1 to 3
production .rs files with 10 to 150 added production lines — lines inside
`#[cfg(test)]` items not counted — changes an integration-test file, and leaves
Cargo.toml and Cargo.lock alone (the agent builds offline and cannot add a
dependency). Candidates are drawn in draw.py's seeded order, at most 4 per
repository; each is validated in its own tree: the commit's non-production files
over the parent must leave at least one test in those files failing that the full
commit passes (fail-to-pass), with every test in those files passing at the commit.
Only tests in test files count; inline unit tests in production files are the
agent's to change and are not run as hidden tests. A repository whose first
DROP_AFTER validated candidates all fail is dropped: a build or test suite that
does not work offline here would otherwise spend the draw on it. Every candidate tried is written
to <work>/tried.jsonl with why it was kept or not; the kept ones to <work>/tasks.json.
"""

import json
import os
import random
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cargo   # noqa: E402
import draw    # noqa: E402  tree, overlay, task_text, earlier, SEED, START, END
mine = cargo.mine
DROP_AFTER = 5


def candidates(clones):
    out = []
    for name in sorted(os.listdir(clones)):
        git = mine.Git(os.path.join(clones, name))
        for c in mine.commits(git):
            if not (draw.START <= c["ct"] < draw.END) or not c["parent"]:
                continue
            paths = [r[4] for r in c["raw"]]
            if any(os.path.basename(p) in cargo.MANIFESTS for p in paths):
                continue
            prod = [r for r in c["raw"] if cargo.production(r[4])]
            if not (1 <= len(prod) <= 3 and any(cargo.is_test(p) for p in paths)):
                continue
            if sum(c["numstat"].get(r[4], (0, 0))[0] for r in prod) < 10:
                continue
            added = sum(cargo.added_production_lines(git.blob(r[0]), git.blob(r[1])) for r in prod)
            if 10 <= added <= 150:
                out.append(dict(repo=name, sha=c["sha"], parent=c["parent"], message=c["message"],
                                files=[[r[2], r[3], r[4]] for r in c["raw"]], added=added))
        git.cat.kill()
    return out


def validate(clones, work, cand):
    clone = os.path.join(clones, cand["repo"])
    root = os.path.join(work, "validate", cand["repo"] + "-" + cand["sha"][:8])
    cargo.tree(clone, cand["parent"], root)
    recipe, why = cargo.fetch(root)
    if recipe is None:
        shutil.rmtree(root, ignore_errors=True)
        return None, why
    others = [p for _, _, p in cand["files"] if not cargo.production(p)]
    prods = [p for _, _, p in cand["files"] if cargo.production(p)]
    deleted = {q for s, _, q in cand["files"] if s == "D"}
    tests = [p for p in others if cargo.is_test(p) and p not in deleted]
    target_dir = os.path.join(cargo.TARGETS, cand["repo"])
    try:
        draw.overlay(clone, cand["sha"], root, others)
        targets = cargo.test_targets(root, tests)
        if not targets:
            return None, "no runnable test target"
        before = cargo.run_tests(root, targets, target_dir)
        draw.overlay(clone, cand["sha"], root, prods)
        after = cargo.run_tests(root, targets, target_dir)
        # a target whose tests are behind a feature runs none by default: every feature, then
        silent = [t for t in targets if not any(i.startswith("%s:%s::" % t[:2]) for i in after)]
        if silent:
            retry = [(p, n, cargo.ALL) for p, n, _ in silent]
            after.update(cargo.run_tests(root, retry, target_dir))
            draw.overlay(clone, cand["parent"], root, prods)
            before.update(cargo.run_tests(root, retry, target_dir))
            draw.overlay(clone, cand["sha"], root, prods)
            targets = [t for t in targets if t not in silent] + retry
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if not after:
        return None, "tests did not run at the commit"
    if not all(after.values()):
        return None, "%d test(s) fail at the commit" % sum(1 for v in after.values() if not v)
    f2p = sorted(t for t, ok in after.items() if ok and not before.get(t, False))
    p2p = sorted(t for t, ok in after.items() if ok and before.get(t, False))
    if not f2p:
        return None, "no fail-to-pass test"
    return dict(recipe=recipe, tests=tests, targets=targets, f2p=f2p, p2p=p2p, overlay=others), "ok"


def main(clones, work, count=30, per_repo=draw.PER_REPO, before=None, used=None, limit=None):
    count, per_repo = int(count), int(per_repo)
    clones, work = os.path.abspath(clones), os.path.abspath(work)
    known = draw.earlier(before)
    skip = {t["sha"] for t in json.load(open(os.path.join(used, "tasks.json")))["tasks"]} if used else set()
    os.makedirs(work, exist_ok=True)
    pool = candidates(clones)
    random.Random(draw.SEED).shuffle(pool)
    print("pool %d" % len(pool), flush=True)
    kept, taken, tried, failed = [], {}, 0, {}
    with open(os.path.join(work, "tried.jsonl"), "a" if limit else "w") as log:
        for cand in pool:
            if len(kept) >= count or (limit and tried >= int(limit)):
                break
            if cand["sha"] in skip or taken.get(cand["repo"], 0) >= per_repo:
                continue
            if failed.get(cand["repo"], 0) >= DROP_AFTER and not taken.get(cand["repo"]):
                continue
            tried += 1
            started = time.time()
            if cand["sha"] in known and (known[cand["sha"]][0] != "ok" or known[cand["sha"]][1]):
                why, task = known[cand["sha"]]
                result = task and {k: task[k] for k in ("recipe", "tests", "targets", "f2p", "p2p", "overlay")}
            else:
                try:
                    result, why = validate(clones, work, cand)
                except subprocess.TimeoutExpired:
                    result, why = None, "timeout"
            seconds = round(time.time() - started)
            log.write(json.dumps(dict(repo=cand["repo"], sha=cand["sha"], outcome=why, seconds=seconds)) + "\n")
            log.flush()
            print(cand["repo"], cand["sha"][:8], why, "%ds" % seconds, flush=True)
            if result:
                text, source = draw.task_text(cand)
                kept.append(dict(cand, **result, text=text, source=source, id="%s-%s" % (cand["repo"], cand["sha"][:8])))
                taken[cand["repo"]] = taken.get(cand["repo"], 0) + 1
            else:
                failed[cand["repo"]] = failed.get(cand["repo"], 0) + 1
                if failed[cand["repo"]] == DROP_AFTER and not taken.get(cand["repo"]):
                    log.write(json.dumps(dict(repo=cand["repo"], outcome="repository dropped after %d failures" % DROP_AFTER)) + "\n")
    with open(os.path.join(work, "tasks.json"), "w") as handle:
        json.dump(dict(pool=len(pool), seed=draw.SEED, tasks=kept), handle, indent=1)
    print("pool %d, kept %d" % (len(pool), len(kept)))


if __name__ == "__main__":
    main(*sys.argv[1:])

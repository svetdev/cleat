#!/usr/bin/env python3
"""pairs — one tree with a clone and the same tree with the clone removed, the same change asked of both.

  pairs.py check <clones dir> <work dir> <base tree> <tasks dir>
  pairs.py run   <clones dir> <work dir> <base tree> <tasks dir> [repeats] [jobs] [only]

target.py compares the arms' trees, which differ in more than the one clone. Here the two trees
differ in nothing else. A task is a directory:

  task.txt          the change, stated as a rule with one example that reaches the first copy only
  tests.rs          hidden tests, `probe_first_*` for the first copy's path, `probe_second_*` for the other
  dedup.patch       the base with both copies replaced by one shared piece, behaviour unchanged
  reference.patch   the change made in full on the base, to prove the tests can pass

`two` is the base tree, `one` the base with dedup.patch. `check` runs no agent: the tests must
fail on both trees and pass on the base with reference.patch. `run` asks the agent for the change
on each tree with the tests hidden; the measure is whether the second copy's tests pass.
Writes <work>/pairs[-<model>]/<task>/<one|two>/<repeat>/score.json.
"""

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chain               # noqa: E402
import lang as languages   # noqa: E402
import probe               # noqa: E402
import run                 # noqa: E402
import draw                # noqa: E402
import score               # noqa: E402
import target              # noqa: E402


def tasks_in(directory, only=""):
    out = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if os.path.isfile(os.path.join(path, "task.txt")) and (not only or name in only.split(",")):
            tests = open(os.path.join(path, "tests.rs")).read()
            out.append(dict(name=name, path=path, text=open(os.path.join(path, "task.txt")).read().strip(),
                            tests=tests, ids=target.TEST_FN.findall(tests)))
    return out


def tree(lang, clone, plan, base, task, kind, root):
    """The base stripped as a probe's tree is, with the task's patch for `kind` laid on."""
    shutil.rmtree(root, ignore_errors=True)
    draw.copy_tree(base, root)
    probe.strip(lang, clone, plan, root)
    patch = {"one": "dedup.patch", "reference": "reference.patch"}.get(kind)
    if patch:
        subprocess.run(["git", "-C", root, "apply", os.path.join(task["path"], patch)], check=True, capture_output=True)
        chain.commit(root, "upstream")


def measured(lang, clone, plan, base, task, kind, root):
    try:
        tree(lang, clone, plan, base, task, kind, root)
        target.lay(root, [task["tests"]])
        return target.suite(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def baseline(lang, clone, plan, work, base, task, kind):
    path = os.path.join(work, "pairs", "baseline", "%s-%s.json" % (task["name"], kind))
    if not os.path.isfile(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        got = measured(lang, clone, plan, base, task, kind, path[:-5] + "-repo")
        with open(path, "w") as handle:
            json.dump(got, handle, indent=1)
    return json.load(open(path))


def one(clones, work, plan, base, task, kind, repeat, before):
    lang = languages.get(plan["lang"])
    out_dir = os.path.join(work, "pairs" + ("-" + run.MODEL if "STUDY_MODEL" in os.environ else ""), task["name"], kind, str(repeat))
    if os.path.isfile(os.path.join(out_dir, "score.json")):
        return task["name"], kind, repeat, "cached"
    if run.PARKED.is_set():
        return task["name"], kind, repeat, "skipped: usage limit"
    os.makedirs(out_dir, exist_ok=True)
    clone = os.path.join(clones, plan["repo"])
    root = os.path.join(out_dir, "repo")
    tree(lang, clone, plan, base, task, kind, root)
    chain.commit(root, "upstream")
    info = dict(task=task["name"], tree=kind, model=run.MODEL)
    info["agent"] = chain.agent(lang, plan, dict(text=task["text"]), clones, work, out_dir, root, out_dir, chain.recipe(plan))
    if run.limited(out_dir, info["agent"]):
        run.PARKED.set()
        return task["name"], kind, repeat, "parked: usage limit, run again after it resets"
    info["run"] = score.run_result(out_dir)
    info["diff"] = subprocess.run(["git", "-C", root, "diff", "HEAD", "--stat"], capture_output=True, text=True).stdout[-1500:]
    copy = os.path.join(out_dir, "check")
    try:
        shutil.rmtree(copy, ignore_errors=True)
        draw.copy_tree(root, copy)
        target.lay(copy, [task["tests"]])
        info["judged"] = target.judged(task, before, target.suite(copy))
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    with open(os.path.join(out_dir, "score.json"), "w") as handle:
        json.dump(info, handle, indent=1)
    lang.drop_build(root)
    tests = info["judged"]["tests"]
    return task["name"], kind, repeat, "first %s second %s" % tuple(
        "%d/%d" % (sum(bool(v) for k, v in tests.items() if k.startswith(p)), sum(k.startswith(p) for k in tests))
        for p in ("probe_first", "probe_second"))


def main(mode, clones, work, base, directory, repeats="3", jobs="6", only=""):
    clones, work, base, directory = (os.path.abspath(p) for p in (clones, work, base, directory))
    chain.guard(work)
    plan = chain.load(work)
    lang = languages.get(plan["lang"])
    clone = os.path.join(clones, plan["repo"])
    tasks = tasks_in(directory, only)
    kinds = ("two", "one", "reference") if mode == "check" else ("two", "one")
    with concurrent.futures.ThreadPoolExecutor(int(jobs)) as pool:
        cells = [(t, k) for t in tasks for k in kinds]
        got = dict(zip([(t["name"], k) for t, k in cells],
                       pool.map(lambda c: baseline(lang, clone, plan, work, base, c[0], c[1]), cells)))
        for task in tasks:
            for kind in kinds:
                mine = target.judged(task, {}, got[(task["name"], kind)])
                print("baseline", task["name"], kind, "task tests passing %d of %d;" % (mine["passed"], mine["of"]),
                      "suite %d of %d" % (sum(got[(task["name"], kind)].values()), len(got[(task["name"], kind)])), flush=True)
        if mode != "run":
            return
        jobs_list = [(t, k, r) for t in tasks for k in kinds for r in range(1, int(repeats) + 1)]
        for result in pool.map(lambda j: one(clones, work, plan, base, j[0], j[1], j[2], got[(j[0]["name"], j[1])]), jobs_list):
            print(*result, flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])

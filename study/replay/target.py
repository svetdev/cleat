#!/usr/bin/env python3
"""target — written changes aimed at the code the arms left different, on every arm's tree.

  target.py <clones dir> <work dir> <checkpoint> <tasks dir> [repeats] [jobs] [only]

probe.py's changes are upstream's next ones, and few of them touch a function or a clone that
one arm has and the other does not. A task here is written for one such place: <name>.txt is
what the agent is asked, <name>.rs the tests that judge it, laid into tests/zz_probe.rs.

  cc-<name>      a function over the complexity ceiling in both `plain` trees and under it in both
                 `cleat` trees must change. The tests are in the tree before the agent starts, as
                 in probe.py's visible mode: the cost is how hard the tree is to change.
  clone-<name>   behaviour that `plain` holds in several copies and `cleat` in fewer must change.
                 The text gives one example and the tests stay hidden, one per copy: the measure
                 is how many copies the agent reached.

Each tree is stripped as probe.py strips it. Before any agent runs, the whole suite with every
task's tests runs on each tree (<work>/targets/baseline/): the tests it passed there and fails
after a run are that run's regressions. Rust only, one integration target.
Writes <work>/targets[-<model>]/<task>/<tree>/<repeat>/score.json.
"""

import concurrent.futures
import json
import os
import re
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
import cargo               # noqa: E402

ARMS = tuple(os.environ.get("STUDY_TREES", "plain-1,plain-2,cleat-1,cleat-2").split(","))
GATED = bool(os.environ.get("STUDY_ATTACH"))   # cleat attached afresh to the stripped tree: how an agent gets past a refusal
CLEAT = os.path.dirname(os.path.dirname(HERE))
TEST_FILE, MODULE = "tests/zz_probe.rs", "zz_probe"
TEST_FN = re.compile(r"#\[test\]\s*fn (\w+)")


def tasks_in(directory, only=""):
    names = sorted(n[:-4] for n in os.listdir(directory) if n.endswith(".txt"))
    out = []
    for name in [n for n in names if not only or n in only.split(",")]:
        tests = open(os.path.join(directory, name + ".rs")).read()
        out.append(dict(name=name, text=open(os.path.join(directory, name + ".txt")).read().strip(), tests=tests,
                        ids=TEST_FN.findall(tests), visible=name.startswith("cc-")))
    return out


def lay(root, bodies):
    """The tasks' tests as one module of the integration target."""
    with open(os.path.join(root, TEST_FILE), "w") as handle:
        handle.write("use super::*;\n\n" + "\n".join(bodies))
    path = os.path.join(root, "tests", "lib.rs")
    text = open(path).read()
    if "mod %s;" % MODULE not in text:
        with open(path, "w") as handle:
            handle.write(text.rstrip("\n") + "\nmod %s;\n" % MODULE)


def suite(root):
    """{test id: passed} for the target that holds the tasks' tests, on a tree that is thrown away."""
    targets = cargo.test_targets(root, [TEST_FILE])
    return cargo.run_tests(root, targets, os.path.join(root, "target"))


def out_base(work):
    label = os.environ.get("STUDY_LABEL")   # a second gated round, under another cleat, kept apart from the first
    return os.path.join(work, "targets" + ("-gated" if GATED else "") + ("-" + label if label else "")
                        + ("-" + run.MODEL if "STUDY_MODEL" in os.environ else ""))


def attach(root):
    out = subprocess.run(["python3", os.path.join(CLEAT, "quality", "bin", "attach.py"), "--into", root],
                         capture_output=True, text=True, env=run.clean_env())
    if out.returncode != 0:
        raise SystemExit("cleat's attach failed: " + (out.stdout + out.stderr)[-800:])
    chain.commit(root, "attach cleat")


def gate_after(root):
    """The hook's own verdict on the tree as the agent left it, and what its event log holds."""
    gate = subprocess.run(["python3", "quality/bin/gate.py", "--changed"], cwd=root, capture_output=True, text=True, env=run.clean_env())
    said = gate.stdout + gate.stderr
    events = [json.loads(line) for line in open(os.path.join(root, "quality", ".events.jsonl"), errors="replace") if line.strip()]
    hooks = [e for e in events if e.get("mode") == "hook"]
    return dict(exit=gate.returncode, failing=sorted({l.split()[1] for l in said.splitlines() if l.split()[:1] == ["FAIL"] and len(l.split()) > 1}),
                tail=said[-2500:], hook_firings=len(hooks), hook_fails=sum(e.get("verdict") == "fail" for e in hooks),
                hook_failing=[[g["name"] for g in e.get("gates", []) if g.get("status") != "ok"] for e in hooks],
                guard_blocked=[e.get("target") for e in events if e.get("mode") == "guard" and e.get("verdict") == "blocked"])


def baseline(lang, clone, plan, work, tree, source, tasks):
    path = os.path.join(work, "targets", "baseline", tree + ".json")
    if os.path.isfile(path):
        return json.load(open(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    root = os.path.join(os.path.dirname(path), tree + "-repo")
    try:
        shutil.rmtree(root, ignore_errors=True)
        draw.copy_tree(source, root)
        probe.strip(lang, clone, plan, root)
        lay(root, [t["tests"] for t in tasks])
        got = suite(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    with open(path, "w") as handle:
        json.dump(got, handle, indent=1)
    return got


def judged(task, base, after):
    """The task's own tests by name, and the tests the tree passed before that fail after."""
    mine = {t: next((ok for k, ok in after.items() if k.endswith("::%s::%s" % (MODULE, t))), None) for t in task["ids"]}
    kept = [k for k, ok in base.items() if ok and "::%s::" % MODULE not in k]
    broken = sorted(k for k in kept if after.get(k) is False)
    return dict(tests=mine, passed=sum(bool(v) for v in mine.values()), of=len(mine),
                resolved=all(mine.values()) and not broken, broken=broken,
                missing=sum(k not in after for k in kept), ran=len(after))


def one(clones, work, plan, tree, source, task, repeat, base):
    lang = languages.get(plan["lang"])
    out_dir = os.path.join(out_base(work), task["name"], tree, str(repeat))
    if os.path.isfile(os.path.join(out_dir, "score.json")):
        return task["name"], tree, repeat, "cached"
    if run.PARKED.is_set():
        return task["name"], tree, repeat, "skipped: usage limit"
    clone = os.path.join(clones, plan["repo"])
    root = os.path.join(out_dir, "repo")
    shutil.rmtree(root, ignore_errors=True)
    draw.copy_tree(source, root)
    probe.strip(lang, clone, plan, root)
    if GATED:
        attach(root)
    asked = dict(text=task["text"])
    if task["visible"]:
        lay(root, [task["tests"]])
        asked["text"] += probe.VISIBLE.format(files=TEST_FILE)
    chain.commit(root, "upstream")
    info = dict(task=task["name"], tree=tree, visible=task["visible"], model=run.MODEL)
    info["agent"] = chain.agent(lang, plan, asked, clones, work, out_dir, root, out_dir, chain.recipe(plan))
    if run.limited(out_dir, info["agent"]):
        run.PARKED.set()
        return task["name"], tree, repeat, "parked: usage limit, run again after it resets"
    info["run"] = score.run_result(out_dir)
    info["diff"] = subprocess.run(["git", "-C", root, "diff", "HEAD", "--stat"], capture_output=True, text=True).stdout[-1500:]
    if GATED:
        info["gate"] = gate_after(root)
    copy = os.path.join(out_dir, "check")
    try:
        shutil.rmtree(copy, ignore_errors=True)
        draw.copy_tree(root, copy)
        lay(copy, [task["tests"]])
        info["judged"] = judged(task, base, suite(copy))
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    with open(os.path.join(out_dir, "score.json"), "w") as handle:
        json.dump(info, handle, indent=1)
    lang.drop_build(root)
    return task["name"], tree, repeat, "passed %d of %d, broken %d" % (
        info["judged"]["passed"], info["judged"]["of"], len(info["judged"]["broken"]))


def main(clones, work, point, directory, repeats="2", jobs="6", only=""):
    clones, work = os.path.abspath(clones), os.path.abspath(work)
    chain.guard(work)
    plan = chain.load(work)
    lang = languages.get(plan["lang"])
    clone = os.path.join(clones, plan["repo"])
    trees = {c: os.path.join(work, "chains", c, "snapshots", point) for c in ARMS}
    every, tasks = tasks_in(directory), tasks_in(directory, only)
    jobs_list = [(tree, source, task, r) for task in tasks for tree, source in trees.items() for r in range(1, int(repeats) + 1)]
    for tree, _, task, r in jobs_list:   # every run's directory exists before any agent starts, so each is denied the others
        os.makedirs(os.path.join(out_base(work), task["name"], tree, str(r)), exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(int(jobs)) as pool:
        bases = dict(zip(trees, pool.map(lambda t: baseline(lang, clone, plan, work, t, trees[t], every), trees)))
        for tree, got in bases.items():
            failing = sum(not ok for k, ok in got.items() if "::%s::" % MODULE in k)
            print("baseline", tree, len(got), "tests,", sum(got.values()), "pass; task tests failing:", failing, flush=True)
        for result in pool.map(lambda j: one(clones, work, plan, j[0], j[1], j[2], j[3], bases[j[0]]), jobs_list):
            print(*result, flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])

#!/usr/bin/env python3
"""recheck — checks for runs scored before the check existed, or before their change was scored.

  recheck.py <clones dir> <work dir> [jobs]

A chain's scored change is checked out from the chain's own history ("change <n>") in an APFS
clone of its tree, so the installed dependencies and build come along; a probe's tree is its
run's repo, reinstalled where its build was dropped. Adds `check_full` (every upstream test
file laid over) to each score.json that lacks it, and `check` to a chain's change that the
plan scores but its run did not (validated after the chain ran). A probe run scored before
probes had baselines gets its full check again, with per-test outcomes, and `judged`.
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
import probe as probe_mod  # noqa: E402
import lang as languages   # noqa: E402
import score               # noqa: E402


def save(path, info):
    with open(path, "w") as handle:
        json.dump(info, handle, indent=1)


def one_chain(clones, work, plan, name):
    lang = languages.get(plan["lang"])
    clone = os.path.join(clones, plan["repo"])
    base = os.path.join(work, "chains", name)
    commits = dict(line.split(" ", 1)[::-1] for line in
                   score.out(os.path.join(base, "repo"), "log", "--format=%H %s").splitlines())
    todo = [c for c in plan["changes"] if c["scored"]
            and os.path.isfile(os.path.join(base, "steps", "%03d" % c["index"], "score.json"))
            and not {"check", "check_full"} <= set(json.load(open(os.path.join(base, "steps", "%03d" % c["index"], "score.json"))))]
    if not todo:
        return name, 0
    root = os.path.join(base, "recheck")
    shutil.rmtree(root, ignore_errors=True)
    subprocess.run(["cp", "-cR", os.path.join(base, "repo"), root], check=True)
    try:
        for change in todo:
            out_dir = os.path.join(base, "steps", "%03d" % change["index"])
            subprocess.run(["git", "checkout", "-q", "-f", commits["change %d" % change["index"]]], cwd=root, check=True)
            subprocess.run(["git", "clean", "-q", "-fd"], cwd=root)
            info = json.load(open(os.path.join(out_dir, "score.json")))
            if "check" not in info:
                info["check"] = chain.check(lang, clone, change, root, out_dir)
            info["check_full"] = chain.check(lang, clone, change, root, out_dir, full=True)
            save(os.path.join(out_dir, "score.json"), info)
            print(name, change["index"], "full resolved=%s" % info["check_full"]["resolved"], flush=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return name, len(todo)


def one_probe(clones, work, plan, point, tree, source, probe, out_dir):
    lang = languages.get(plan["lang"])
    clone = os.path.join(clones, plan["repo"])
    path = os.path.join(out_dir, "score.json")
    root = os.path.join(out_dir, "repo")
    if lang.name == "ts":
        lang.install(root, chain.recipe(plan))
    info = json.load(open(path))
    if "check" not in info:
        info["check"] = chain.check(lang, clone, probe, root, out_dir)
    if "tests" not in info.get("check_full", {}):
        info["check_full"] = chain.check(lang, clone, probe, root, out_dir, full=True)
    info["judged"] = probe_mod.judged(probe, probe_mod.baseline(clones, work, plan, point, tree, source, probe), info["check_full"])
    save(path, info)
    lang.drop_build(root)
    print("probe", point, tree, os.path.basename(out_dir), "judged resolved=%s" % info["judged"]["resolved"], flush=True)


def probes(clones, work, plan, jobs):
    """Probe runs scored before the baseline: the full check again for its per-test outcomes,
    then judged against the tree's baseline, computed once per tree and probe."""
    lang = languages.get(plan["lang"])
    by_index = {p["index"]: p for ps in plan["probes"].values() for p in ps}
    base = os.path.join(work, "probes")
    todo = {}
    for point in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        snaps = {c: os.path.join(work, "chains", c, "snapshots", point) for c in sorted(os.listdir(os.path.join(work, "chains")))}
        trees = probe_mod.probe_trees(lang, plan, clones, work, point, snaps)
        for tree in sorted(os.listdir(os.path.join(base, point))):
            for run in sorted(os.listdir(os.path.join(base, point, tree))):
                out_dir = os.path.join(base, point, tree, run)
                path = os.path.join(out_dir, "score.json")
                if os.path.isfile(path) and "judged" not in json.load(open(path)):
                    probe = by_index[int(run.split("-")[0])]
                    todo.setdefault((point, tree, probe["index"]), []).append(
                        (int(point), tree.lstrip("_"), trees[tree.lstrip("_")], probe, out_dir))
    with concurrent.futures.ThreadPoolExecutor(int(jobs)) as pool:   # a tree's baseline first, then its runs
        list(pool.map(lambda runs: [one_probe(clones, work, plan, *r) for r in runs], todo.values()))


def main(clones, work, jobs="4"):
    clones, work = os.path.abspath(clones), os.path.abspath(work)
    plan = chain.load(work)
    names = sorted(os.listdir(os.path.join(work, "chains")))
    with concurrent.futures.ThreadPoolExecutor(len(names)) as pool:
        list(pool.map(lambda n: one_chain(clones, work, plan, n), names))
    probes(clones, work, plan, jobs)


if __name__ == "__main__":
    main(*sys.argv[1:])

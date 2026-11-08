#!/usr/bin/env python3
"""probe — the same plain agent on every tree at a checkpoint, so the code differs and the agent does not.

  probe.py <clones dir> <work dir> <checkpoint> [repeats] [jobs] [modes]     (modes: hidden,visible)

The trees are each chain's snapshot at the checkpoint and upstream's own tree after the same
change: the oracle chain's snapshot where there is one, as it went through the same harness. Each is copied, stripped of cleat (its quality/ and .claude/ directories, and CLAUDE.md
back to upstream's), and given a fresh one-commit history, so no tree tells the agent which
arm built it. Per probe of the plan's for that checkpoint and per repeat: upstream's
non-production, non-test edits for the probe laid on, the agent run without cleat, and the
probe's tests checked as chain.py checks a scored change. In the hidden mode the agent has
the change's text alone, as a chain's agent does; in the visible mode upstream's test files
for the change are in the tree before it starts and it is asked to make them pass, so every
tree's probe asks the same thing and its cost measures how hard the tree is to change.
A probe comes a few changes past its checkpoint, so before any agent runs, each tree's own
full check at the probe is its baseline (<work>/baselines/), and a run is judged resolved when
the fail-to-pass tests pass and none the tree passed before fails after (`judged`).
Writes <work>/probes/<checkpoint>/<tree>/<probe index>-<repeat>[-visible]/score.json.
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
import run                 # noqa: E402
import draw                # noqa: E402
import score               # noqa: E402


def upstream_tree(lang, plan, clones, work, point):
    """Upstream's tree after change `point`, installed once per checkpoint."""
    root = os.path.join(work, "probes", str(point), "_upstream", "repo")
    if not os.path.isdir(root):
        sha = plan["changes"][point - 1]["sha"]
        chain.tree(os.path.join(clones, plan["repo"]), sha, root)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        lang.install(root, chain.recipe(plan), update=True) if lang.name == "rust" else lang.install(root, chain.recipe(plan))
        with open(os.path.join(root, ".gitignore"), "a") as handle:
            handle.write("\n" + "\n".join(lang.ignored) + "\n")
    return root


def strip(lang, clone, plan, root):
    """No sign of cleat and no history: what the probe's agent sees is the code alone."""
    for name in ("quality", ".claude"):
        shutil.rmtree(os.path.join(root, name), ignore_errors=True)
    draw.overlay(clone, plan["changes"][0]["parent"], root, ["CLAUDE.md"])
    shutil.rmtree(os.path.join(root, ".git"), ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
    chain.commit(root, "base")


def prepare(lang, clone, plan, point, source, probe, root):
    """The tree as a probe's agent finds it: copied, stripped, upstream's other edits for the probe laid on."""
    shutil.rmtree(root, ignore_errors=True)
    draw.copy_tree(source, root)
    strip(lang, clone, plan, root)
    rec = chain.recipe(plan)
    moved, laid = chain.upstream_edits(lang, clone, plan["changes"][point - 1]["sha"], probe, root)
    info = dict(upstream_files=laid)
    if moved:
        rec, info["reinstall"] = lang.install(root, rec)
    return rec, info


def baseline_path(work, point, tree, probe):
    return os.path.join(work, "baselines", str(point), tree, "%03d.json" % probe["index"])


def baseline(clones, work, plan, point, tree, source, probe):
    """The full check on the tree before any agent touches it. A probe comes a few changes past
    its checkpoint, so some of upstream's tests at the probe need changes no tree has; a run is
    judged on the tests its own tree passed before it, not on those."""
    path = baseline_path(work, point, tree, probe)
    if os.path.isfile(path):
        return json.load(open(path))
    lang = languages.get(plan["lang"])
    out_dir = os.path.dirname(path)
    os.makedirs(out_dir, exist_ok=True)
    root = os.path.join(out_dir, "repo-%03d" % probe["index"])
    os.makedirs(root + "-out", exist_ok=True)
    try:
        prepare(lang, os.path.join(clones, plan["repo"]), plan, point, source, probe, root)
        got = chain.check(lang, os.path.join(clones, plan["repo"]), probe, root, root + "-out", full=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(root + "-out", ignore_errors=True)
    with open(path, "w") as handle:
        json.dump(got, handle, indent=1)
    return got


def judged(probe, base, check):
    """Resolved against the tree's own baseline: every fail-to-pass test passes, and no
    pass-to-pass test the tree passed before the agent fails after it."""
    kept = [t for t in probe["p2p"] if base["tests"].get(t)]
    return dict(resolved=all(check["tests"].get(t) for t in probe["f2p"] + kept),
                f2p_passed=sum(bool(check["tests"].get(t)) for t in probe["f2p"]), f2p=len(probe["f2p"]),
                p2p_kept=sum(bool(check["tests"].get(t)) for t in kept), p2p_baseline=len(kept),
                f2p_at_baseline=sum(bool(base["tests"].get(t)) for t in probe["f2p"]))


VISIBLE = ("\n\nUpstream's tests for this change are already in the tree: {files}. Make them pass "
           "without changing them.")


def run_dir(work, point, tree, probe, repeat, mode):
    name = "%03d-%d" % (probe["index"], repeat) + ("-visible" if mode == "visible" else "")
    return os.path.join(work, "probes", str(point), tree, name)


def tests_of(lang, probe):
    return [p for p in probe["overlay"] if lang.is_test(p) or score.mine.summary._is_test(p)]


def one(clones, work, plan, point, tree, source, probe, repeat, mode):
    lang = languages.get(plan["lang"])
    out_dir = run_dir(work, point, tree, probe, repeat, mode)
    if os.path.isfile(os.path.join(out_dir, "score.json")):
        return tree, probe["index"], repeat, mode, "cached"
    if run.PARKED.is_set():
        return tree, probe["index"], repeat, mode, "skipped: usage limit"
    clone = os.path.join(clones, plan["repo"])
    root = os.path.join(out_dir, "repo")
    rec, info = prepare(lang, clone, plan, point, source, probe, root)
    asked = probe
    if mode == "visible":
        tests = tests_of(lang, probe)
        draw.overlay(clone, probe["sha"], root, tests + chain.runner_configs(lang, clone, probe["sha"]))
        asked = dict(probe, text=probe["text"] + VISIBLE.format(files=", ".join(tests)))
    chain.commit(root, "upstream")
    info["agent"] = chain.agent(lang, plan, asked, clones, work, out_dir, root, out_dir, rec)
    if run.limited(out_dir, info["agent"]):
        run.PARKED.set()
        return tree, probe["index"], repeat, mode, "parked: usage limit, run again after it resets"
    info["run"] = score.run_result(out_dir)
    info["mode"] = mode
    info["stray"] = chain.memories(root)
    info["check"] = chain.check(lang, clone, probe, root, out_dir)
    info["check_full"] = chain.check(lang, clone, probe, root, out_dir, full=True)
    info["judged"] = judged(probe, baseline(clones, work, plan, point, tree, source, probe), info["check_full"])
    with open(os.path.join(out_dir, "score.json"), "w") as handle:
        json.dump(info, handle, indent=1)
    lang.drop_build(root)
    return tree, probe["index"], repeat, mode, "resolved=%s full=%s judged=%s" % (
        info["check"]["resolved"], info["check_full"]["resolved"], info["judged"]["resolved"])


def probe_trees(lang, plan, clones, work, point, trees):
    """The chains' snapshots, with the oracle's standing in as upstream's tree."""
    trees = dict(trees)
    oracle = trees.pop("oracle-1", None)   # upstream's code through the same harness as the arms' trees
    trees["upstream"] = oracle or upstream_tree(lang, plan, clones, work, int(point))
    for name in [t for t in trees if t.startswith("oracle")]:
        del trees[name]
    return trees


def main(clones, work, point, repeats="2", jobs="6", modes="hidden,visible"):
    clones, work = os.path.abspath(clones), os.path.abspath(work)
    chain.guard(work)
    plan = chain.load(work)
    lang = languages.get(plan["lang"])
    trees = {c: os.path.join(work, "chains", c, "snapshots", point) for c in sorted(os.listdir(os.path.join(work, "chains")))}
    missing = [c for c, t in trees.items() if not os.path.isdir(t)]
    if missing:   # a probe round with a chain left out would compare the arms on unequal trees
        raise SystemExit("no snapshot at change %s yet: %s" % (point, ", ".join(missing)))
    trees = probe_trees(lang, plan, clones, work, point, trees)
    jobs_list = [(t, s, p, r, m) for t, s in trees.items() for p in plan["probes"][point]
                 for r in range(1, int(repeats) + 1) for m in modes.split(",")]
    for t, _, p, r, m in jobs_list:   # every run's directory exists before any agent starts, so each is denied the others
        os.makedirs(run_dir(work, point, t, p, r, m), exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(int(jobs)) as pool:
        list(pool.map(lambda j: baseline(clones, work, plan, int(point), *j),
                      [(t, s, p) for t, s in trees.items() for p in plan["probes"][point]]))
        for result in pool.map(lambda j: one(clones, work, plan, int(point), *j), jobs_list):
            print(*result, flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])

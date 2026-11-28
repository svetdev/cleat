#!/usr/bin/env python3
"""later — does a tree's own test suite stop a later change that goes wrong?

  later.py <clones dir> <work dir> [mutants per run] [workers] [own | upstream]
  later.py <clones dir> <work dir> proof           (the free checks, before anything is counted)
  later.py report <work dir> …

After probe.py. Starts no agent. A probe is a real later change, made by the same plain agent
on every chain's tree at a checkpoint, and its tree is kept: HEAD is the tree the agent found,
the working tree what it left. Here the later change is made to go wrong: one operator is
flipped (mutate.py's sites) on a production line the probe's agent added or changed, and the
tests that were in the tree BEFORE the probe run against it. Those tests know nothing of the
later change, so one of them failing is a regression stopped; none failing is one that got
through. The tests that count are the ones passing on the later change as the agent left it,
so a test the change itself was meant to break is not a kill.

Per hidden probe run: the checkpoint's snapshot (dependencies installed) is cloned, the
probe's HEAD laid over it, then the agent's production edits; its test edits are left out.
`upstream` runs the same mutants against upstream's test files at the checkpoint instead, laid
over the tree: a reference suite, the same for every arm, that says how catchable the mutants
are. A run whose probe moved a manifest is reinstalled, and left out where that fails.
Rows go to <work>/later/<checkpoint>/<tree>/<run>-<tests>.jsonl, one per mutant, and a
summary beside them; a run with a summary is not done again.

`proof` takes the first run of each arm at each checkpoint and shows the measurement can see
what it claims to: the old suite runs and nine tenths of it passes on the later change, a
second clean run fails nothing the first passed, and a `throw` planted at the top of a changed
production file is seen (a test fails, or none can run). The report leaves out any run where
under nine tenths of the old tests pass: a probe some way past its checkpoint brings newer
dependencies, and old tests that fail for those are not the tree's suite failing.
"""

import collections
import concurrent.futures
import difflib
import glob
import hashlib
import json
import os
import random
import shutil
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lang as languages   # noqa: E402
import chain               # noqa: E402
import draw                # noqa: E402
import mutate              # noqa: E402

SHARE = 0.9
PLANTED = 'throw new Error("later.py planted regression");\n'


def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True).stdout


def runs_of(work):
    """[(checkpoint, tree, run name, run dir)] for every hidden probe run with a score and a kept tree."""
    out = []
    for path in sorted(glob.glob(os.path.join(work, "probes", "*", "*", "*", "score.json"))):
        directory = os.path.dirname(path)
        point, tree, name = directory.split(os.sep)[-3:]
        if not name.endswith("-visible") and not tree.startswith("_") and os.path.isdir(os.path.join(directory, "repo", ".git")):
            out.append((point, tree, name, directory))
    return out


def edits(repo):
    """{path: text or None} the probe's agent left against HEAD, deletions as None."""
    out = {}
    for line in git(repo, "status", "--porcelain", "-uall", "--no-renames").splitlines():
        path = line[3:].strip('"')
        full = os.path.join(repo, path)
        out[path] = open(full, errors="replace").read() if os.path.isfile(full) else None
    return out


def changed_lines(before, after):
    """The line numbers in `after` that `before` does not have in place."""
    out = set()
    matcher = difflib.SequenceMatcher(None, before.split("\n"), after.split("\n"), autojunk=False)
    for tag, _, _, start, end in matcher.get_opcodes():
        if tag in ("replace", "insert"):
            out |= set(range(start + 1, end + 1))
    return out


def lay(root, path, text):
    full = os.path.join(root, path)
    if text is None:
        if os.path.isfile(full):
            os.remove(full)
        return
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as handle:
        handle.write(text)


def snapshot_of(work, point, tree):
    name = "oracle-1" if tree == "upstream" else tree
    return os.path.join(work, "chains", name, "snapshots", point)


def build(lang, plan, work, point, tree, directory, target):
    """(production edits, old test files, problem): the later change in a tree that can run tests,
    with the tests as they were before it."""
    repo = os.path.join(directory, "repo")
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    draw.copy_tree(snapshot_of(work, point, tree), target)
    archive = subprocess.Popen(["git", "-C", repo, "archive", "HEAD"], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", target], stdin=archive.stdout, check=True)
    archive.wait()
    if "reinstall" in json.load(open(os.path.join(directory, "score.json"))):
        _, why = lang.install(target, chain.recipe(plan))
        if why != "ok":
            return {}, [], "the probe moved a manifest and the reinstall failed: %s" % str(why)[:200]
    production = {p: t for p, t in edits(repo).items() if lang.production(p) and not lang.is_test(p)}
    for path, text in production.items():
        lay(target, path, text)
    tests = [p for p in git(repo, "ls-tree", "-r", "--name-only", "HEAD").split("\n") if p and lang.is_test(p)]
    return production, sorted(tests), None


def reference(lang, clone, plan, point, target):
    """Upstream's test files at the checkpoint laid over the tree; the test files among them."""
    sha = plan["changes"][int(point) - 1]["sha"]
    laid = chain.upstream_tests(lang, clone, sha)
    draw.overlay(clone, sha, target, laid + chain.runner_configs(lang, clone, sha))
    return sorted(p for p in laid if lang.is_test(p) and os.path.isfile(os.path.join(target, p)))


def places(lang, repo, target, production):
    """The operator sites on the production lines the later change added or changed."""
    wanted = set()
    for path, text in production.items():
        if text is not None:
            shown = subprocess.run(["git", "-C", repo, "show", "HEAD:" + path], capture_output=True, text=True)
            wanted |= {(path, n) for n in changed_lines(shown.stdout if shown.returncode == 0 else "", text)}
    files = mutate.mutable(lang, sorted(p for p, t in production.items() if t is not None))
    return sorted(site for site in mutate.all_sites(lang, target, files) if mutate.line_of(target, site) in wanted)


def tests_run(lang, target, files, label):
    """{test id: passed}, or None when the run took too long."""
    if lang.name == "ts" and languages.vitest_projects(target):
        return mutate.run_tests(lang, target, files)
    return lang.run_tests(target, dict(tests=files), label)


def verdict(got, counted):
    if got is None:
        return "killed"
    if not got:
        return "invalid"
    return "killed" if any(got.get(test) is not True for test in counted) else "survived"


def planted(lang, target, path, files, counted):
    """The verdict on one file with `PLANTED` written at its top."""
    full = os.path.join(target, path)
    text = open(full).read()
    with open(full, "w") as handle:
        handle.write(PLANTED + text)
    try:
        return verdict(tests_run(lang, target, files, "planted"), counted)
    finally:
        with open(full, "w") as handle:
            handle.write(text)


def flipped(lang, target, site, files, counted):
    path, at, old, new = site
    full = os.path.join(target, path)
    text = open(full).read()
    with open(full, "w") as handle:
        handle.write(text[:at] + new + text[at + len(old):])
    try:
        return verdict(tests_run(lang, target, files, "mutant"), counted)
    finally:
        with open(full, "w") as handle:
            handle.write(text)


def prepared(context, job, tests, target):
    """(row, sites, test files, counted tests) for one run: the tree built and its clean run."""
    lang, plan, clone, work = context
    point, tree, name, directory = job
    row = dict(point=point, tree=tree, run=name, tests=tests, sites=0, tried=0, killed=0, invalid=0)
    production, files, row["problem"] = build(lang, plan, work, point, tree, directory, target)
    if row["problem"]:
        return row, [], [], []
    if tests == "upstream":
        files = reference(lang, clone, plan, point, target)
    lang.before_check(target)
    found = places(lang, os.path.join(directory, "repo"), target, production)
    clean = tests_run(lang, target, files, "clean") or {}
    counted = sorted(test for test, ok in clean.items() if ok)
    row.update(sites=len(found), old_tests=len(clean), counted=len(counted),
               production_files=sum(text is not None for text in production.values()))
    return row, found, files, counted


def one(context, job, tests, count):
    lang, _, _, work = context
    point, tree, name, _ = job
    base = os.path.join(work, "later", point, tree)
    kept = os.path.join(base, "%s-%s.json" % (name, tests))
    if os.path.isfile(kept):
        return json.load(open(kept))
    target = os.path.join(work, "later", "w", "%s-%s-%s-%s" % (point, tree, name, tests))
    os.makedirs(base, exist_ok=True)
    try:
        row, found, files, counted = prepared(context, job, tests, target)
        seed = int(hashlib.sha1(("%s/%s/%s" % (point, tree, name)).encode()).hexdigest(), 16)
        picked = sorted(random.Random(seed).sample(found, min(count, len(found)))) if counted else []
        with open(os.path.join(base, "%s-%s.jsonl" % (name, tests)), "w") as handle:
            for site in picked:
                status = flipped(lang, target, site, files, counted)
                row["invalid" if status == "invalid" else "tried"] += 1
                row["killed"] += status == "killed"
                handle.write(json.dumps(dict(file=site[0], at=site[1], old=site[2], new=site[3], status=status)) + "\n")
    finally:
        shutil.rmtree(target, ignore_errors=True)
    with open(kept, "w") as handle:
        json.dump(row, handle)
    return row


def proof_one(context, job):
    """One run through the free checks; the problem found, or None."""
    lang, _, _, work = context
    point, tree, name, _ = job
    target = os.path.join(work, "later", "w", "proof-%s-%s-%s" % (point, tree, name))
    try:
        row, found, files, counted = prepared(context, job, "own", target)
        if row["problem"]:
            return row["problem"]
        if len(counted) < SHARE * max(row["old_tests"], 1):
            return "%d of %d old tests pass on the later change" % (len(counted), row["old_tests"])
        again = tests_run(lang, target, files, "again") or {}
        flaky = [test for test in counted if again.get(test) is not True]
        if flaky:
            return "a second clean run fails %d test(s) the first passed: %s" % (len(flaky), flaky[:2])
        changed = sorted({site[0] for site in found})
        if not changed:
            return None   # nothing to flip here: the run adds no site and no score
        status = planted(lang, target, changed[0], files, counted)
        return None if status != "survived" else "a throw at the top of %s went unseen" % changed[0]
    finally:
        shutil.rmtree(target, ignore_errors=True)


def proof(context, jobs, workers):
    first = {}
    for job in jobs:
        first.setdefault((job[0], job[1].split("-")[0]), job)
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        problems = list(pool.map(lambda job: proof_one(context, job), first.values()))
    for job, problem in zip(first.values(), problems):
        print("%-4s %-9s %-8s %s" % (job[0], job[1], job[2], problem or "proven"), flush=True)
    print("%d of %d runs proven" % (sum(p is None for p in problems), len(problems)))
    return 0 if not any(problems) else 1


def rows_of(work, tests):
    """Each run's summary. One where under SHARE of the old tests pass on the later change is left
    out: the probe moved the dependencies under them, and what is left is not the tree's suite."""
    rows = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(work, "later", "*", "*", "*-%s.json" % tests)))]
    for row in rows:
        if not row.get("problem") and row.get("counted", 0) < SHARE * max(row.get("old_tests", 0), 1):
            row.update(problem="%d of %d old tests pass on the later change" % (row.get("counted", 0), row.get("old_tests", 0)), tried=0, killed=0)
    return rows


def rate(rows):
    tried = sum(r["tried"] for r in rows)
    return sum(r["killed"] for r in rows) / tried if tried else None


def arm_line(arm, rows):
    scored = [r for r in rows if r["tried"]]
    return "  %-8s %3d runs, %3d left out, %3d with a site | old tests counted, median %5d | stopped %4d of %4d (%.1f%%)" % (
        arm, len(rows), sum(bool(r.get("problem")) for r in rows), len(scored),
        statistics.median([r["counted"] for r in scored] or [0]), sum(r["killed"] for r in scored),
        sum(r["tried"] for r in scored), 100 * (rate(scored) or 0))


def gaps(rows):
    """Per later change (checkpoint and probe), the gated trees' rate less the ungated trees'."""
    by = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        by[r["point"], r["run"].split("-")[0]][r["tree"].split("-")[0]].append(r)
    out = {}
    for key, arms in sorted(by.items()):
        cleat, plain = rate(arms.get("cleat", [])), rate(arms.get("plain", []))
        if cleat is not None and plain is not None:
            out[key] = cleat - plain
    return out


def report(*works):
    for work in works:
        for tests in ("own", "upstream"):
            rows = rows_of(work, tests)
            if not rows:
                continue
            print("%s, %s tests from before the later change" % (os.path.basename(os.path.normpath(work)), tests))
            for arm in ("plain", "cleat", "upstream"):
                print(arm_line(arm, [r for r in rows if r["tree"].split("-")[0] == arm]))
            found = gaps(rows)
            print("  cleat less plain over %d later changes scored in both: higher in %d, lower in %d, mean gap %+.1f points" % (
                len(found), sum(g > 0 for g in found.values()), sum(g < 0 for g in found.values()),
                100 * statistics.mean(found.values()) if found else 0))
            late = {k: g for k, g in found.items() if int(k[0]) >= 90}
            if late and len(late) < len(found):
                print("  at checkpoints 90 and later, %d changes: mean gap %+.1f points" % (len(late), 100 * statistics.mean(late.values())))


def main(clones, work, count="8", workers="4", tests="own"):
    if clones == "report":
        return report(*[w for w in (work, count, workers, tests) if os.path.isdir(w)])
    clones, work = os.path.abspath(clones), os.path.abspath(work)
    plan = chain.load(work)
    lang = languages.get(plan["lang"])
    context = (lang, plan, os.path.join(clones, plan["repo"]), work)
    jobs = runs_of(work)
    if count == "proof":
        return proof(context, jobs, int(workers))
    with concurrent.futures.ThreadPoolExecutor(int(workers)) as pool:
        for row in pool.map(lambda job: one(context, job, tests, int(count)), jobs):
            print("%-4s %-9s %-8s %-8s sites %3d, stopped %d of %d%s" % (
                row["point"], row["tree"], row["run"], tests, row["sites"], row["killed"], row["tried"],
                "  " + row["problem"] if row.get("problem") else ""), flush=True)
    return report(work)


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))

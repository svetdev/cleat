#!/usr/bin/env python3
"""mutate — how many planted bugs a tree's tests catch, on the chains' trees at a checkpoint.

  mutate.py <clones dir> <work dir> <checkpoint> [mutants per tree] [workers] [written | control]

Per tree (each chain's snapshot at the checkpoint, the oracle's as upstream) and per test set
(upstream's test files at the checkpoint's change laid over the tree, or the tree's own), the
tests that pass on the unmutated tree are the ones that count. Mutants are sampled with a
fixed seed from the operator sites on the production lines the replay added or changed since
its base (all of them where there are fewer), which TypeScript's own
parser finds in code that runs, never in a type (mutsites.js): a comparison, equality,
`&&`/`||`, `+`/`-`, a boolean literal, a dropped `!`. Each mutant runs the
test set: killed when a counted test fails or stops running (a timeout too), invalid when the
run reports no test, survived otherwise. zod's packages keep vitest's type-checking on whatever
the command line says (`--typecheck.enabled=false` reaches only the root), but a type error in
the source is an unhandled error, not a failed test, so only a test can kill a mutant. Results go to
<work>/mutants/<checkpoint>/<tree>-<tests>.jsonl, one line per mutant (…-control.jsonl for the
control: the same mutants in every tree, from the files no tree changed); a mutant with a line
is not run again. TypeScript trees whose vitest runs from the root only (zod).
"""

import concurrent.futures
import bisect
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lang as languages   # noqa: E402
import chain               # noqa: E402
import draw                # noqa: E402
import tsdraw              # noqa: E402

SEED = 90
TIMEOUT = 300


def sites(lang, root):
    """[(file, offset, old, new)] for every operator site on the tree's production lines the replay wrote."""
    listed = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split("\n")
    files = sorted(p for p in listed if p.endswith(".ts") and not p.endswith(".d.ts") and lang.production(p))
    lines = written(root)
    return sorted(site for site in all_sites(lang, root, files) if line_of(root, site) in lines)


def all_sites(lang, root, files):
    """[(file, offset, old, new)] for every operator site in `files`."""
    out = []
    for i in range(0, len(files), 200):
        got = subprocess.run(["node", os.path.join(HERE, "mutsites.js"), os.path.join(root, "node_modules", "typescript"),
                              root, *files[i:i + 200]], capture_output=True, text=True, check=True)
        out += [tuple(site) for site in json.loads(got.stdout)]
    return out


def base_of(root):
    return next(line.split()[0] for line in subprocess.run(["git", "log", "--format=%H %s"], cwd=root, capture_output=True,
                                                          text=True).stdout.splitlines() if line.split(" ", 1)[1:] == ["base"])


def written(root):
    """{(file, line)} of the production lines the replay added or changed since its base."""
    diff = subprocess.run(["git", "diff", "-U0", "--no-renames", base_of(root), "HEAD"], cwd=root, capture_output=True, text=True).stdout
    out, path = set(), None
    for line in diff.split("\n"):
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and path:
            start, _, count = re.search(r"\+(\d+)(,(\d+))?", line).groups()
            out |= {(path, n) for n in range(int(start), int(start) + (int(count) if count is not None else 1))}
    return out


LINES = {}


def line_of(root, site):
    path, at = site[0], site[1]
    if (root, path) not in LINES:
        text = open(os.path.join(root, path), errors="replace").read()
        LINES[root, path] = [i for i, c in enumerate(text) if c == "\n"]
    return path, 1 + bisect.bisect_left(LINES[root, path], at)


def run_tests(root, files):
    """{test id: passed} for `files`, vitest from the root, type-checking off."""
    output = os.path.join(root, ".mutreport.json")
    if os.path.exists(output):
        os.remove(output)
    cmd = [tsdraw.binary_for(root, root, "vitest"), "run", *files, "--typecheck.enabled=false",
           "--reporter=json", "--outputFile=" + output]
    try:
        subprocess.run(cmd, cwd=root, capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None
    return tsdraw.parse(tsdraw.read_json(output), root, "vitest")


def prepare(lang, clone, plan, point, snapshot, tests, target):
    """A copy of the snapshot, upstream's test files laid over for `upstream`; the test files to run."""
    shutil.rmtree(target, ignore_errors=True)
    subprocess.run(["cp", "-cR", snapshot, target], check=True)
    sha = plan["changes"][point - 1]["sha"]
    if tests == "upstream":
        laid = chain.upstream_tests(lang, clone, sha)
        draw.overlay(clone, sha, target, laid + chain.runner_configs(lang, clone, sha))
        files = [p for p in laid if tsdraw.is_test(p)]
    else:
        listed = subprocess.run(["git", "ls-files"], cwd=target, capture_output=True, text=True).stdout.split("\n")
        files = [p for p in listed if p and tsdraw.is_test(p)]
    lang.before_check(target)
    return sorted(p for p in files if os.path.isfile(os.path.join(target, p)))


def shared(lang, snapshots):
    """{snapshot: sites} on the production lines no tree changed since its base, translations aside
    (they have hardly a test), the same sites in every tree and in the same order: a site is known
    by its file, its line's text, which copy of that line it is and its place on the line. The
    control: the same mutants in every tree, so the arms should kill alike."""
    keyed = {}
    for root in snapshots:
        listed = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split("\n")
        files = sorted(p for p in listed if p.endswith(".ts") and not p.endswith(".d.ts") and lang.production(p)
                       and "/locales/" not in p)
        lines, out = written(root), {}
        for site in all_sites(lang, root, files):
            path, at = site[0], site[1]
            where = line_of(root, site)
            if where in lines:
                continue
            text = open(os.path.join(root, path), errors="replace").read()
            start = text.rfind("\n", 0, at) + 1
            line = text[start:text.find("\n", at) if text.find("\n", at) >= 0 else len(text)]
            copy = text.count("\n" + line + "\n", 0, start + len(line) + 1)
            out[(path, line, copy, at - start, site[2])] = site
        keyed[root] = out
    keys = sorted(set.intersection(*(set(k) for k in keyed.values())))
    return {root: [keyed[root][k] for k in keys] for root in snapshots}


def one(lang, clone, plan, point, work, tree, snapshot, tests, n, workers, control=None):
    base = os.path.join(work, "mutants", str(point))
    os.makedirs(base, exist_ok=True)
    out = os.path.join(base, "%s-%s%s.jsonl" % (tree, tests, "-control" if control else ""))
    done = {json.loads(line)["id"] for line in open(out)} if os.path.exists(out) else set()
    found = control or sites(lang, snapshot)
    picked = sorted(random.Random(SEED).sample(found, min(n, len(found))))
    todo = [(i, s) for i, s in enumerate(picked) if i not in done]
    copies = [os.path.join(base, "w", "%s-%s-%d" % (tree, tests, k)) for k in range(workers)]
    os.makedirs(os.path.join(base, "w"), exist_ok=True)
    files = prepare(lang, clone, plan, point, snapshot, tests, copies[0])
    baseline = run_tests(copies[0], files) or {}
    counted = sorted(t for t, ok in baseline.items() if ok)
    for c in copies[1:]:
        prepare(lang, clone, plan, point, snapshot, tests, c)
    lock, free = threading.Lock(), list(copies)

    def mutant(item):
        i, (path, at, old, new) = item
        with lock:
            root = free.pop()
        try:
            source = os.path.join(root, path)
            text = open(source).read()
            with open(source, "w") as handle:
                handle.write(text[:at] + new + text[at + len(old):])
            got = run_tests(root, files)
            with open(source, "w") as handle:
                handle.write(text)
        finally:
            with lock:
                free.append(root)
        status = ("killed" if got is None else "invalid" if not got else
                  "killed" if any(got.get(t) is not True for t in counted) else "survived")
        row = dict(id=i, file=path, at=at, old=old, new=new, status=status)
        with lock, open(out, "a") as handle:
            handle.write(json.dumps(row) + "\n")
        return status

    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        list(pool.map(mutant, todo))
    for c in copies:
        shutil.rmtree(c, ignore_errors=True)
    rows = [json.loads(line) for line in open(out)]
    killed, survived = sum(r["status"] == "killed" for r in rows), sum(r["status"] == "survived" for r in rows)
    print("%s %s: %d tests counted of %d; killed %d, survived %d, invalid %d; score %.1f%%" % (
        tree, tests, len(counted), len(baseline), killed, survived, len(rows) - killed - survived,
        100.0 * killed / (killed + survived) if killed + survived else 0), flush=True)


def main(clones, work, point, n="400", workers="6", kind="written"):
    """`kind` control: the same mutants in every tree, from the files none changed (shared)."""
    clones, work, point = os.path.abspath(clones), os.path.abspath(work), int(point)
    plan = chain.load(work)
    lang = languages.get(plan["lang"])
    clone = os.path.join(clones, plan["repo"])
    names = sorted(n for n in os.listdir(os.path.join(work, "chains"))
                   if os.path.isdir(os.path.join(work, "chains", n, "snapshots", str(point))))
    snapshots = [os.path.join(work, "chains", name, "snapshots", str(point)) for name in names]
    control = shared(lang, snapshots) if kind == "control" else {}
    for name, snapshot in zip(names, snapshots):
        tree = "upstream" if name.startswith("oracle") else name
        for tests in ("upstream", "own"):
            one(lang, clone, plan, point, work, tree, snapshot, tests, int(n), int(workers), control.get(snapshot))


if __name__ == "__main__":
    main(*sys.argv[1:])

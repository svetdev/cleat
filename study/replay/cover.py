#!/usr/bin/env python3
"""cover — line and branch coverage of the code the replay wrote, beside mutate's scores.

  cover.py <clones dir> <work dir> <checkpoint> <coverage package dir>

Per tree and test set, as mutate.py prepares them: the tests run once with vitest's v8
coverage over the production files the replay changed since its base, and the report is read
for the lines it wrote (mutate.written): the share of those lines' statements that ran, and
of the branches starting on them that were taken. The coverage provider is not in the trees
and the agents had no network, so it comes from <coverage package dir>/node_modules (an
`npm install @vitest/coverage-v8@<the trees' vitest>`), copied into each tree's copy.
Results go to <work>/coverage/<checkpoint>.jsonl.
"""

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lang as languages   # noqa: E402
import chain               # noqa: E402
import mutate              # noqa: E402
import tsdraw              # noqa: E402


def provide(tool, root):
    """The coverage provider and what it needs, copied in where the tree lacks them."""
    for scope in os.listdir(os.path.join(tool, "node_modules")):
        if scope.startswith("."):
            continue
        names = [os.path.join(scope, n) for n in os.listdir(os.path.join(tool, "node_modules", scope))] if scope.startswith("@") else [scope]
        for name in names:
            target = os.path.join(root, "node_modules", name)
            if not os.path.exists(target):
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copytree(os.path.join(tool, "node_modules", name), target, symlinks=True)


def measure(root, files, include, out_dir):
    shutil.rmtree(out_dir, ignore_errors=True)
    cmd = [tsdraw.binary_for(root, root, "vitest"), "run", *files,
           "--coverage.enabled=true", "--coverage.provider=v8", "--coverage.reporter=json", "--coverage.reportOnFailure=true",
           "--coverage.reportsDirectory=" + out_dir, *("--coverage.include=" + p for p in include)]
    subprocess.run(cmd, cwd=root, capture_output=True, timeout=1800)
    path = os.path.join(out_dir, "coverage-final.json")
    return json.load(open(path)) if os.path.exists(path) else {}


def shares(report, root, lines):
    """(statements on written lines that ran, of how many; branches starting on them taken, of how many)."""
    ran = stmts = taken = branches = 0
    for path, f in report.items():
        rel = os.path.relpath(path, root)
        for k, loc in f["statementMap"].items():
            if (rel, loc["start"]["line"]) in lines:
                stmts += 1
                ran += f["s"][k] > 0
        for k, b in f["branchMap"].items():
            if (rel, b["loc"]["start"]["line"]) in lines:
                branches += len(f["b"][k])
                taken += sum(n > 0 for n in f["b"][k])
    return ran, stmts, taken, branches


def main(clones, work, point, tool):
    clones, work, point, tool = os.path.abspath(clones), os.path.abspath(work), int(point), os.path.abspath(tool)
    plan = chain.load(work)
    lang = languages.get(plan["lang"])
    clone = os.path.join(clones, plan["repo"])
    os.makedirs(os.path.join(work, "coverage"), exist_ok=True)
    out = os.path.join(work, "coverage", "%d.jsonl" % point)
    for name in sorted(os.listdir(os.path.join(work, "chains"))):
        snapshot = os.path.join(work, "chains", name, "snapshots", str(point))
        if not os.path.isdir(snapshot):
            continue
        lines = {site for site in mutate.written(snapshot)}
        include = sorted({p for p, _ in lines if p.endswith(".ts") and not p.endswith(".d.ts") and lang.production(p)})
        for tests in ("upstream", "own"):
            copy = os.path.join(work, "coverage", "w-%s-%s" % (name, tests))
            files = mutate.prepare(lang, clone, plan, point, snapshot, tests, copy)
            provide(tool, copy)
            report = measure(copy, files, include, os.path.join(copy, ".coverage"))
            ran, stmts, taken, branches = shares(report, copy, lines)
            shutil.rmtree(copy, ignore_errors=True)
            row = dict(tree="upstream" if name.startswith("oracle") else name, tests=tests, files=len(report),
                       statements=stmts, ran=ran, branches=branches, taken=taken)
            print("%s %s: lines %.1f%% of %d statements, branches %.1f%% of %d" % (
                row["tree"], tests, 100.0 * ran / stmts if stmts else 0, stmts, 100.0 * taken / branches if branches else 0,
                branches), flush=True)
            with open(out, "a") as handle:
                handle.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:])

#!/usr/bin/env python3
"""tsdraw — the paired-run tasks for TypeScript, by the Python rule as near as it goes.

  tsdraw.py <clones dir> <work dir> [count] [per repository] [earlier work dir] [used work dir]

A candidate is a non-merge commit from 2024-01-01 to 2025-06-30 whose production code
files (cleat's test/aside rule, over .ts .tsx .mts .cts .js .jsx .mjs .cjs, outside build
output directories dist, deno_dist and build) are 1 to 3,
all TypeScript, with 10 to 150 added lines among them, and which changes a test file
(`*.test.*`, `*.spec.*` or under `__tests__/`). Candidates are drawn in the Python draw's
seeded order rule, at most 4 per repository; each is validated in its own tree: the
dependencies installed by the repository's own package manager and lockfile, then the
commit's non-production files laid over the parent must leave at least one test failing
that the full commit passes (fail-to-pass), with every test in those files passing at the
commit. Tests run with the repository's own runner (vitest, jest or mocha) from the
nearest directory that configures it, through a JSON reporter. As in draw.py, every
candidate tried goes to <work>/tried.jsonl and the kept ones to <work>/tasks.json.
"""

import datetime
import json
import os
import random
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import draw   # noqa: E402  the Python draw: tree, overlay, task text, earlier outcomes, seed
import mine   # noqa: E402  (draw put the JIT study on the path)
import summary   # noqa: E402  cleat's test/aside rule (mine put quality/bin on the path)

START, END, SEED, PER_REPO = draw.START, draw.END, draw.SEED, draw.PER_REPO
TS = (".ts", ".tsx", ".mts", ".cts")
CODE = TS + (".js", ".jsx", ".mjs", ".cjs")
TEST_NAME = re.compile(r"\.(?:test|spec)\.[cm]?[jt]sx?$")
TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), ".study-work", "ts-tools")
STORE = os.path.join(os.path.dirname(TOOLS), "ts-store")
INSTALL_TIMEOUT = 1200
TEST_TIMEOUT = 900
ENV_EXTRA = {"CI": "1", "HUSKY": "0", "PUPPETEER_SKIP_DOWNLOAD": "1", "PUPPETEER_SKIP_CHROMIUM_DOWNLOAD": "1",
             "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1", "CYPRESS_INSTALL_BINARY": "0", "ELECTRON_SKIP_BINARY_DOWNLOAD": "1",
             "FORCE_COLOR": "0", "NO_COLOR": "1"}


BUILT = {"dist", "deno_dist", "build"}   # generated copies of the sources (hono's deno_dist), not code anyone writes


def production(path):
    """Production code, any JavaScript dialect: what is not a test, example, doc or benchmark,
    nor a build output directory's copy."""
    return path.endswith(CODE) and not summary._is_test(path) and not BUILT.intersection(path.split("/")[:-1])


def typescript(path):
    return path.endswith(TS)


def is_test(path):
    return path.endswith(CODE) and summary._is_test(path) and not summary._aside(path) and (
        bool(TEST_NAME.search(os.path.basename(path))) or "__tests__" in path.split("/"))


def candidates(clones):
    out = []
    for name in sorted(os.listdir(clones)):
        git = mine.Git(os.path.join(clones, name))
        for c in mine.commits(git):
            if not (START <= c["ct"] < END) or not c["parent"]:
                continue
            prod = [r for r in c["raw"] if production(r[4])]
            added = sum(c["numstat"].get(r[4], (0, 0))[0] for r in prod)
            if 1 <= len(prod) <= 3 and all(typescript(r[4]) for r in prod) and 10 <= added <= 150 \
                    and any(is_test(r[4]) for r in c["raw"]):
                out.append(dict(repo=name, sha=c["sha"], parent=c["parent"], message=c["message"],
                                files=[[r[2], r[3], r[4]] for r in c["raw"]]))
        git.cat.kill()
    return out


def env(extra_path=()):
    e = {k: v for k, v in os.environ.items() if not (k.startswith("CLAUDE") or k == "CLAUDECODE" or k.startswith("npm_"))}
    e.update(ENV_EXTRA)
    e["PATH"] = ":".join(list(extra_path) + ["/opt/homebrew/bin", e.get("PATH", "")])
    return e


def sh(cmd, cwd=None, timeout=TEST_TIMEOUT, extra_path=()):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env(extra_path))
    except subprocess.TimeoutExpired:
        return "timeout", ""
    return p.returncode, (p.stdout[-3000:] + p.stderr[-3000:])


def read_json(path):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


# --- package managers -------------------------------------------------------------------

def manager(root):
    """(name, version spec) the tree's own lockfile and packageManager field call for."""
    pkg = read_json(os.path.join(root, "package.json")) or {}
    declared = (pkg.get("packageManager") or "").split("+")[0]
    if os.path.isfile(os.path.join(root, "pnpm-lock.yaml")):
        if declared.startswith("pnpm@"):
            return "pnpm", declared[5:]
        head = open(os.path.join(root, "pnpm-lock.yaml"), errors="replace").read(200)
        version = re.search(r"lockfileVersion:\s*'?([\d.]+)", head)
        major = {"9": "9", "6": "8", "5": "7"}.get((version.group(1) if version else "9").split(".")[0], "9")
        return "pnpm", major
    if os.path.isfile(os.path.join(root, "yarn.lock")):
        if declared.startswith("yarn@"):
            return "yarn", declared[5:]
        if "__metadata:" in open(os.path.join(root, "yarn.lock"), errors="replace").read(2000):
            return "yarn", "berry"
        return "yarn", "1.22.22"
    if os.path.isfile(os.path.join(root, "package-lock.json")):
        return "npm", None
    return None, None


def tool_bin(name, version):
    """The directory holding `name`@`version`'s binary, installed once into .study-work/ts-tools."""
    if name == "npm" or version is None:
        return None
    prefix = os.path.join(TOOLS, "%s@%s" % (name, version))
    binary = os.path.join(prefix, "node_modules", ".bin")
    if not os.path.isfile(os.path.join(binary, name)):
        os.makedirs(prefix, exist_ok=True)
        package = "@yarnpkg/cli-dist@%s" % version if name == "yarn" and not version.startswith("1.") else "%s@%s" % (name, version)
        sh(["npm", "install", "--prefix", prefix, "--no-audit", "--no-fund", "--silent", package], timeout=600)
    return binary if os.path.isfile(os.path.join(binary, name)) else None


def install(root):
    """node_modules for the tree with its own manager and lockfile; the recipe that worked."""
    name, version = manager(root)
    if not name:
        return None, "no lockfile"
    if name == "yarn" and version == "berry":
        releases = sorted(os.listdir(os.path.join(root, ".yarn", "releases"))) if os.path.isdir(os.path.join(root, ".yarn", "releases")) else []
        if not releases:
            return None, "yarn berry without a pinned release"
        version = re.sub(r"^yarn-|\.c?js$", "", releases[-1])
    binary = tool_bin(name, version)
    if name != "npm" and not binary:
        return None, "could not install %s@%s" % (name, version)
    if name == "pnpm":
        tries = [["pnpm", "install", "--frozen-lockfile", "--store-dir", STORE, "--config.confirmModulesPurge=false"],
                 ["pnpm", "install", "--no-frozen-lockfile", "--store-dir", STORE, "--config.confirmModulesPurge=false"]]
    elif name == "yarn" and version.startswith("1."):
        tries = [["yarn", "install", "--frozen-lockfile", "--non-interactive", "--ignore-engines"],
                 ["yarn", "install", "--non-interactive", "--ignore-engines"]]
    elif name == "yarn":
        tries = [["yarn", "install", "--immutable"], ["yarn", "install"]]
    else:
        tries = [["npm", "ci", "--no-audit", "--no-fund"], ["npm", "ci", "--no-audit", "--no-fund", "--legacy-peer-deps"],
                 ["npm", "install", "--no-audit", "--no-fund", "--legacy-peer-deps"]]
    said = ""
    for cmd in tries:
        code, said = sh(cmd, cwd=root, timeout=INSTALL_TIMEOUT, extra_path=[binary] if binary else [])
        if code == 0:
            return dict(manager=name, version=version, cmd=cmd), "ok"
    return None, "install failed (%s): %s" % (" ".join(tries[-1][:2]), said[-300:])


def install_recipe(root, recipe):
    binary = tool_bin(recipe["manager"], recipe["version"])
    return sh(recipe["cmd"], cwd=root, timeout=INSTALL_TIMEOUT, extra_path=[binary] if binary else [])


def recipe_path(recipe):
    """PATH entries the agent needs to reach the tree's own package manager."""
    binary = tool_bin(recipe["manager"], recipe["version"]) if recipe else None
    return [binary] if binary else []


# --- test runners -----------------------------------------------------------------------

CONFIGS = {"vitest": re.compile(r"^vitest\.(?:config|workspace)\.[cm]?[jt]s$|^vitest\.workspace\.json$"),
           "jest": re.compile(r"^jest\.config\.[cm]?[jt]s$|^jest\.config\.json$"),
           "mocha": re.compile(r"^\.mocharc(?:\.[cm]?js|\.json|\.ya?ml)?$")}


def runner_of(directory):
    """The runner a directory configures, or None."""
    names = os.listdir(directory)
    for runner, pattern in CONFIGS.items():
        if any(pattern.match(n) for n in names):
            return runner
    for n in names:
        if re.match(r"^vite\.config\.[cm]?[jt]s$", n) and re.search(r"\btest\s*:", open(os.path.join(directory, n), errors="replace").read()):
            return "vitest"
    pkg = read_json(os.path.join(directory, "package.json")) or {}
    if "jest" in pkg:
        return "jest"
    deps = dict(pkg.get("devDependencies") or {}, **(pkg.get("dependencies") or {}))
    return next((r for r in ("vitest", "jest", "mocha") if r in deps), None)


def binary_for(root, start, runner):
    d = start
    while True:
        b = os.path.join(d, "node_modules", ".bin", runner)
        if os.path.exists(b):
            return b
        if os.path.normpath(d) == os.path.normpath(root):
            return None
        d = os.path.dirname(d)


def locate(root, test_file):
    """(runner, the directory it runs from) for one test file: the nearest one configuring a runner."""
    d = os.path.dirname(os.path.join(root, test_file))
    while True:
        if os.path.isdir(d):
            runner = runner_of(d)
            if runner and binary_for(root, d, runner):
                return runner, d
        if os.path.normpath(d) == os.path.normpath(root):
            return None, None
        d = os.path.dirname(d)


def command(runner, binary, files, output):
    if runner == "vitest":
        return [binary, "run", *files, "--reporter=json", "--outputFile=" + output]
    if runner == "jest":
        return [binary, "--ci", "--json", "--outputFile=" + output, "--runTestsByPath", *files]
    return [binary, "--reporter", "json", "--reporter-option", "output=" + output, *files]


def parse(report, root, runner):
    """{test id: passed} from a jest-shaped (vitest, jest) or mocha JSON report."""
    out = {}
    if not report:
        return out
    if runner == "mocha":
        for t in report.get("tests", []):
            if t.get("pending"):
                continue
            out["%s::%s" % (os.path.relpath(t.get("file", "?"), root), t.get("fullTitle"))] = not t.get("err")
        return out
    for suite in report.get("testResults", []):
        rel = os.path.relpath(suite.get("name") or suite.get("testFilePath") or "?", root)
        for a in suite.get("assertionResults") or suite.get("testResults") or []:
            if a.get("status") not in ("passed", "failed"):
                continue
            name = " > ".join(list(a.get("ancestorTitles") or []) + [a.get("title") or ""])
            out["%s::%s" % (rel, name)] = a["status"] == "passed"
    return out


def run_tests(root, files, label):
    """{test id: passed} for the tests in `files`, each group run from its runner's directory."""
    groups = {}
    for f in files:
        runner, where = locate(root, f)
        if runner:
            groups.setdefault((runner, where), []).append(f)
    out = {}
    for i, ((runner, where), group) in enumerate(sorted(groups.items())):
        output = os.path.join(root, ".tsreport-%s-%d.json" % (label, i))
        rel = [os.path.relpath(os.path.join(root, f), where) for f in group]
        sh(command(runner, binary_for(root, where, runner), rel, output), cwd=where, timeout=TEST_TIMEOUT)
        out.update(parse(read_json(output), root, runner))
        if os.path.exists(output):
            os.remove(output)
    return out


def hint(root, files):
    """How the agent can run the task's tests: the runner and the directory it runs from."""
    runner, where = locate(root, files[0])
    if not runner:
        return None
    rel = os.path.relpath(where, root)
    cmd = {"vitest": "npx vitest run <test file>", "jest": "npx jest <test file>", "mocha": "npx mocha <test file>"}[runner]
    return cmd if rel == "." else "cd %s && %s" % (rel, cmd)


def validate(clones, work, cand):
    clone = os.path.join(clones, cand["repo"])
    root = os.path.join(work, "validate", cand["repo"] + "-" + cand["sha"][:8])
    try:
        draw.tree(clone, cand["parent"], root)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)   # install hooks expect a repository
        recipe, why = install(root)
        if recipe is None:
            return None, why
        others = [p for _, _, p in cand["files"] if not production(p)]
        prods = [p for _, _, p in cand["files"] if production(p)]
        deleted = {q for s, _, q in cand["files"] if s == "D"}
        tests = [p for p in others if is_test(p) and p not in deleted]
        if not tests:
            return None, "no runnable test file"
        draw.overlay(clone, cand["sha"], root, others)
        if not any(locate(root, t)[0] for t in tests):
            return None, "no test runner found"
        before = run_tests(root, tests, "before")
        draw.overlay(clone, cand["sha"], root, prods)
        after = run_tests(root, tests, "after")
        if not after:
            return None, "tests did not run at the commit"
        if not all(after.values()):
            return None, "%d test(s) fail at the commit" % sum(1 for v in after.values() if not v)
        f2p = sorted(t for t, ok in after.items() if ok and not before.get(t, False))
        p2p = sorted(t for t, ok in after.items() if ok and before.get(t, False))
        if not f2p:
            return None, "no fail-to-pass test"
        return dict(recipe=recipe, tests=tests, f2p=f2p, p2p=p2p, overlay=others, hint=hint(root, tests)), "ok"
    finally:
        shutil.rmtree(root, ignore_errors=True)   # node_modules are large; nothing of a tried tree is kept


def main(clones, work, count=10, per_repo=PER_REPO, before=None, used=None):
    count, per_repo = int(count), int(per_repo)
    clones, work = os.path.abspath(clones), os.path.abspath(work)
    known = draw.earlier(before)
    skip = {t["sha"] for t in json.load(open(os.path.join(used, "tasks.json")))["tasks"]} if used else set()
    os.makedirs(work, exist_ok=True)
    pool = candidates(clones)
    random.Random(SEED).shuffle(pool)
    kept, taken = [], {}
    with open(os.path.join(work, "tried.jsonl"), "w") as log:
        for cand in pool:
            if len(kept) >= count:
                break
            if cand["sha"] in skip or taken.get(cand["repo"], 0) >= per_repo:
                continue
            started = datetime.datetime.now()
            if cand["sha"] in known and (known[cand["sha"]][0] != "ok" or known[cand["sha"]][1]):
                why, task = known[cand["sha"]]
                result = task and {k: task[k] for k in ("recipe", "tests", "f2p", "p2p", "overlay", "hint")}
            else:
                try:
                    result, why = validate(clones, work, cand)
                except subprocess.TimeoutExpired:
                    result, why = None, "timeout"
            seconds = (datetime.datetime.now() - started).total_seconds()
            log.write(json.dumps(dict(repo=cand["repo"], sha=cand["sha"], outcome=why, seconds=round(seconds))) + "\n")
            log.flush()
            print(cand["repo"], cand["sha"][:8], why.splitlines()[0][:120] if why else why, "%ds" % seconds, flush=True)
            if result:
                text, source = draw.task_text(cand)
                kept.append(dict(cand, **result, text=text, source=source, id="%s-%s" % (cand["repo"], cand["sha"][:8])))
                taken[cand["repo"]] = taken.get(cand["repo"], 0) + 1
    with open(os.path.join(work, "tasks.json"), "w") as handle:
        json.dump(dict(pool=len(pool), seed=SEED, tasks=kept), handle, indent=1)
    print("pool %d, kept %d" % (len(pool), len(kept)))


if __name__ == "__main__":
    main(*sys.argv[1:])

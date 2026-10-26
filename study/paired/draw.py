#!/usr/bin/env python3
"""draw — draw the paired-run tasks by the rule pre-registered on 2026-10-25.

  draw.py <clones dir> <work dir> [count] [per repository] [earlier work dir]

A candidate is a non-merge commit from 2024-01-01 to 2025-06-30 that changes 1 to
3 production Python files with 10 to 150 added production lines and changes a
test file. Candidates are drawn in seeded order, at most 2 per repository; each is
validated in its own virtualenv: the commit's non-production files laid over the
parent must leave at least one test failing that the full commit passes
(fail-to-pass), with every test in those files passing at the commit. Every
candidate tried is written to <work>/tried.jsonl with why it was kept or not;
the kept ones, with their task text, to <work>/tasks.json. An earlier work dir's
outcomes are reused for the candidates it tried, so a scaled draw follows the
same order without validating again.
"""

import datetime
import json
import os
import random
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "jit"))
import mine   # noqa: E402  the JIT study's commit reader and production rule

START = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
END = datetime.datetime(2025, 7, 1, tzinfo=datetime.timezone.utc).timestamp()
SEED = 20261025
PER_REPO = 2
PYTHON = "/opt/homebrew/bin/python3.12" if os.path.exists("/opt/homebrew/bin/python3.12") else sys.executable
EXTRAS = ("test", "tests", "testing", "dev")
REQUIREMENTS = ("requirements.txt", "requirements/tests.txt", "requirements/test.txt", "requirements-dev.txt", "requirements/dev.txt",
                "test-requirements.txt", "requirements-test.txt", "requirements-tests.txt", "dev-requirements.txt",
                "tests/requirements.txt", "requirements/tests.in", "requirements/testing.txt")


def is_test(path):
    return path.endswith(".py") and not mine.production(path) and "test" in path.lower()


def candidates(clones):
    out = []
    for name in sorted(os.listdir(clones)):
        git = mine.Git(os.path.join(clones, name))
        for c in mine.commits(git):
            if not (START <= c["ct"] < END) or not c["parent"]:
                continue
            prod = [r for r in c["raw"] if mine.production(r[4])]
            added = sum(c["numstat"].get(r[4], (0, 0))[0] for r in prod)
            if 1 <= len(prod) <= 3 and 10 <= added <= 150 and any(is_test(r[4]) for r in c["raw"]):
                out.append(dict(repo=name, sha=c["sha"], parent=c["parent"], message=c["message"],
                                files=[[r[2], r[3], r[4]] for r in c["raw"]]))
        git.cat.kill()
    return out


def sh(cmd, cwd=None, timeout=900, env=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    return p.returncode, p.stdout[-4000:] + p.stderr[-4000:]


def tree(clone, sha, target):
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target)
    archive = subprocess.Popen(["git", "-C", clone, "archive", sha], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", target], stdin=archive.stdout, check=True)
    archive.wait()


def install(root):
    """A venv at root/.venv with the project and its test dependencies; the recipe that worked."""
    venv = os.path.join(root, ".venv")
    sh([PYTHON, "-m", "venv", venv])
    pip = [os.path.join(venv, "bin", "python"), "-m", "pip", "install", "-q", "--disable-pip-version-check"]
    sh(pip + ["-U", "pip"])
    recipe = []
    for extra in EXTRAS:
        code, said = sh(pip + ["-e", ".[%s]" % extra], cwd=root)
        if code == 0 and "does not provide the extra" not in said:   # pip only warns on an unknown extra
            recipe.append("-e .[%s]" % extra)
            break
    else:
        code, out = sh(pip + ["-e", "."], cwd=root)
        if code:
            return None, "install failed: " + out[-300:]
        recipe.append("-e .")
    for group in EXTRAS:
        if sh(pip + ["--group", group], cwd=root)[0] == 0:
            recipe.append("--group %s" % group)
    for req in REQUIREMENTS:
        if os.path.isfile(os.path.join(root, req)) and sh(pip + ["-r", req], cwd=root)[0] == 0:
            recipe.append("-r %s" % req)
    sh(pip + ["pytest"], cwd=root)
    recipe.append("pytest")
    return recipe, "ok"


def install_recipe(root, recipe):
    venv = os.path.join(root, ".venv")
    sh([PYTHON, "-m", "venv", venv])
    pip = [os.path.join(venv, "bin", "python"), "-m", "pip", "install", "-q", "--disable-pip-version-check"]
    sh(pip + ["-U", "pip"])
    for step in recipe:
        sh(pip + step.split(), cwd=root)


def pytest(root, files, label):
    """{test id: passed} for the tests in `files`, run from root with its venv."""
    xml = os.path.join(root, ".junit-%s.xml" % label)
    sh([os.path.join(root, ".venv", "bin", "python"), "-m", "pytest", "-p", "no:cacheprovider", "-q",
        "--junitxml", xml, *files], cwd=root, timeout=900)
    if not os.path.isfile(xml):
        return {}
    out = {}
    for case in ET.parse(xml).getroot().iter("testcase"):
        tid = "%s::%s" % (case.get("classname"), case.get("name"))
        bad = any(child.tag in ("failure", "error") for child in case)
        skipped = any(child.tag == "skipped" for child in case)
        if not skipped:
            out[tid] = not bad
    os.remove(xml)
    return out


def overlay(clone, sha, root, paths):
    for path in paths:
        code = subprocess.run(["git", "-C", clone, "show", "%s:%s" % (sha, path)], capture_output=True)
        target = os.path.join(root, path)
        if code.returncode == 0:
            os.makedirs(os.path.dirname(target) or root, exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(code.stdout)
        elif os.path.exists(target):
            os.remove(target)


def validate(clones, work, cand):
    clone = os.path.join(clones, cand["repo"])
    root = os.path.join(work, "validate", cand["repo"] + "-" + cand["sha"][:8])
    tree(clone, cand["parent"], root)
    recipe, why = install(root)
    if recipe is None:
        return None, why
    others = [p for _, _, p in cand["files"] if not mine.production(p)]
    prods = [p for _, _, p in cand["files"] if mine.production(p)]
    deleted = {q for s, _, q in cand["files"] if s == "D"}
    tests = [p for p in others if is_test(p) and p not in deleted
             and (os.path.basename(p).startswith("test") or p.endswith("_test.py"))]
    if not tests:
        return None, "no runnable test file"
    overlay(clone, cand["sha"], root, others)
    before = pytest(root, tests, "before")
    overlay(clone, cand["sha"], root, prods)
    after = pytest(root, tests, "after")
    if not after:
        return None, "tests did not run at the commit"
    if not all(after.values()):
        return None, "%d test(s) fail at the commit" % sum(1 for v in after.values() if not v)
    f2p = sorted(t for t, ok in after.items() if ok and not before.get(t, False))
    p2p = sorted(t for t, ok in after.items() if ok and before.get(t, False))
    if not f2p:
        return None, "no fail-to-pass test"
    shutil.rmtree(root, ignore_errors=True)
    return dict(recipe=recipe, tests=tests, f2p=f2p, p2p=p2p, overlay=others), "ok"


DIFF_BLOCK = re.compile(r"```\s*(?:diff|patch)\b.*?```", re.S | re.I)
LINKED = re.compile(r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+#(\d+)", re.I)


def gh(path):
    p = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    return json.loads(p.stdout) if p.returncode == 0 and p.stdout.strip() else None


def task_text(cand):
    owner_repo = cand["repo"].replace("__", "/", 1)
    pulls = gh("repos/%s/commits/%s/pulls" % (owner_repo, cand["sha"])) or []
    if not pulls:
        return cand["message"].strip(), "commit message"
    pr = pulls[0]
    text = "%s\n\n%s" % (pr.get("title") or "", pr.get("body") or "")
    source = "pull request #%d" % pr["number"]
    linked = LINKED.search(pr.get("body") or "") or LINKED.search(pr.get("title") or "")
    if linked:
        issue = gh("repos/%s/issues/%s" % (owner_repo, linked.group(1)))
        if issue and "pull_request" not in issue:
            text += "\n\n---\nLinked issue #%s: %s\n\n%s" % (linked.group(1), issue.get("title") or "", issue.get("body") or "")
            source += " + issue #%s" % linked.group(1)
    return DIFF_BLOCK.sub("[diff removed]", text).strip(), source


def earlier(directory):
    """{sha: (outcome, kept task or None)} from an earlier draw."""
    if not directory:
        return {}
    kept = {t["sha"]: t for t in json.load(open(os.path.join(directory, "tasks.json")))["tasks"]}
    return {r["sha"]: (r["outcome"], kept.get(r["sha"]))
            for r in map(json.loads, open(os.path.join(directory, "tried.jsonl")))}


def main(clones, work, count=10, per_repo=PER_REPO, before=None):
    count, per_repo = int(count), int(per_repo)
    clones, work = os.path.abspath(clones), os.path.abspath(work)
    known = earlier(before)
    os.makedirs(work, exist_ok=True)
    pool = candidates(clones)
    random.Random(SEED).shuffle(pool)
    kept, taken = [], {}
    with open(os.path.join(work, "tried.jsonl"), "w") as log:
        for cand in pool:
            if len(kept) >= count:
                break
            if taken.get(cand["repo"], 0) >= per_repo:
                continue
            if cand["sha"] in known and (known[cand["sha"]][0] != "ok" or known[cand["sha"]][1]):
                why, task = known[cand["sha"]]
                result = task and {k: task[k] for k in ("recipe", "tests", "f2p", "p2p", "overlay")}
            else:
                try:
                    result, why = validate(clones, work, cand)
                except subprocess.TimeoutExpired:
                    result, why = None, "timeout"
            log.write(json.dumps(dict(repo=cand["repo"], sha=cand["sha"], outcome=why)) + "\n")
            log.flush()
            print(cand["repo"], cand["sha"][:8], why, flush=True)
            if result:
                text, source = task_text(cand)
                kept.append(dict(cand, **result, text=text, source=source, id="%s-%s" % (cand["repo"], cand["sha"][:8])))
                taken[cand["repo"]] = taken.get(cand["repo"], 0) + 1
    with open(os.path.join(work, "tasks.json"), "w") as handle:
        json.dump(dict(pool=len(pool), seed=SEED, tasks=kept), handle, indent=1)
    print("pool %d, kept %d" % (len(pool), len(kept)))


if __name__ == "__main__":
    main(*sys.argv[1:])

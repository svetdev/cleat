#!/usr/bin/env python3
"""rules — how often did an agent break a rule the project itself keeps?

  rules.py <out.json> <clones dir>:<language>:<paired work dir> [… more]      (STUDY_RUFF names the ruff binary)

Reads runs that paired/run.py, tsrun.py or rrun.py already made; starts no agent. The gates
studied elsewhere hold the code's shape. These are the rules a project states for itself,
where breaking one is harm by definition. Per run, over the production files the agent changed:

  public API   a signature the project exported before the run (cleat's own reader,
               extractors/surface.py) that the agent's tree no longer has, and that upstream's
               own commit for the same task kept. One upstream dropped too is the task's.
  tests        a test that passed before the run and fails after it (the run's own score).
  lint         Python, where the project configures ruff: a violation `ruff check` reports in
               a changed file that it did not report for that file before (`--no-fix`: a project
               whose config says `fix = true` would otherwise have ruff repair the copy it reads).
  format       Python, where the project's pre-commit names ruff-format or black: a changed
               file `ruff format --check` passed before and does not pass after.

Printed per work dir and arm: runs, and runs that broke each. An arm with cleat attached was
held to none of these but the tests, so its rate beside the ungated arm's says whether the
default gates catch any of it by accident.
"""

import collections
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "quality", "bin"))
import summary                  # noqa: E402  cleat's own test-file rule
from extractors import surface  # noqa: E402

RUFF = os.environ.get("STUDY_RUFF", "ruff")
RUFF_CONFIGS = ("pyproject.toml", "ruff.toml", ".ruff.toml", "setup.cfg")


def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, errors="replace")


def changed(root, suffixes):
    """The production files the agent's uncommitted work touched."""
    names = set(git(root, "diff", "HEAD", "--name-only").stdout.split()) | set(git(root, "ls-files", "--others", "--exclude-standard").stdout.split())
    return sorted(n for n in names if n.endswith(suffixes) and not summary._is_test(n) and not n.startswith(("quality/", ".claude/")))


def lay(directory, files):
    """`files` ({name: text or None}) written under a fresh directory."""
    for name, text in files.items():
        if text is not None:
            path = os.path.join(directory, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write(text)
    return directory


def versions(root, clone, sha, names):
    """({name: text} before the run, after it, and at upstream's commit); None where absent."""
    def shown(repo, ref, name):
        out = git(repo, "show", "%s:%s" % (ref, name))
        return out.stdout if out.returncode == 0 else None

    def now(name):
        path = os.path.join(root, name)
        return open(path, errors="replace").read() if os.path.isfile(path) else None
    return ({n: shown(root, "HEAD", n) for n in names}, {n: now(n) for n in names}, {n: shown(clone, sha, n) for n in names})


def signatures(files, language, scratch):
    directory = lay(tempfile.mkdtemp(dir=scratch), files)
    return {(name, sig) for name, _, sig in surface.built_in([directory], language, directory)}


def api_breaks(before, after, upstream, language, scratch):
    """Signatures exported before, gone after, that upstream's commit still has."""
    had, has, kept = (signatures(v, language, scratch) for v in (before, after, upstream))
    return sorted((had - has) & kept)


def ruff(directory, names, *args):
    present = [n for n in names if os.path.isfile(os.path.join(directory, n))]
    if not present:
        return None
    return subprocess.run([RUFF, *args, *present], cwd=directory, capture_output=True, text=True)


def lint(root, before, after, scratch):
    """(new ruff violations, files whose formatting broke), or None for a check the project does not run."""
    configs = {c: open(os.path.join(root, c), errors="replace").read() for c in RUFF_CONFIGS if os.path.isfile(os.path.join(root, c))}
    configured = any("[tool.ruff" in t for c, t in configs.items() if c == "pyproject.toml") or any(c.endswith("ruff.toml") for c in configs)
    hooks = os.path.join(root, ".pre-commit-config.yaml")
    formats = os.path.isfile(hooks) and any(w in open(hooks, errors="replace").read() for w in ("ruff-format", "black"))
    trees = [lay(tempfile.mkdtemp(dir=scratch), dict(configs, **v)) for v in (before, after)]
    new, unformatted = None, None
    if configured:
        found = []
        for tree in trees:
            out = ruff(tree, list(after), "check", "--no-fix", "--output-format", "json", "--no-cache")
            try:
                found.append(collections.Counter((v["filename"][len(os.path.realpath(tree)) + 1:], v["code"]) for v in json.loads(out.stdout or "[]")) if out else collections.Counter())
            except ValueError:
                return None, None   # the project's config is one this ruff cannot read
        new = sorted((found[1] - found[0]).elements())
    if formats:
        clean = []
        for tree in trees:   # one file at a time: the exit code is the verdict, whatever this ruff prints
            clean.append({n for n in after if os.path.isfile(os.path.join(tree, n))
                          and ruff(tree, [n], "format", "--check", "--no-cache").returncode == 0})
        unformatted = sorted(n for n in clean[0] if n not in clean[1] and after[n] is not None)
    return new, unformatted


def one(directory, task, clone, language, scratch):
    root = os.path.join(directory, "repo")
    score = json.load(open(os.path.join(directory, "score.json"))).get("score", {})
    names = changed(root, surface.SUFFIXES[language])
    before, after, upstream = versions(root, clone, task["sha"], names)
    row = dict(task=task["id"], arm=os.path.basename(directory), files=len(names), resolved=bool(score.get("resolved")),
               broke_tests=score.get("p2p_passed", 0) < score.get("p2p", 0),
               api=["%s: %s" % b for b in api_breaks(before, after, upstream, language, scratch)])
    if language == "python":
        row["lint"], row["format"] = lint(root, before, after, scratch)
    return row


def report(label, rows):
    print("\n%s" % label)
    by_arm = collections.defaultdict(list)
    for row in rows:
        by_arm[row["arm"].split("-")[0]].append(row)
    for arm, mine in sorted(by_arm.items()):
        parts = ["%-7s %3d runs" % (arm, len(mine)),
                 "public API broken %2d" % sum(bool(r["api"]) for r in mine),
                 "passing test broken %2d" % sum(r["broke_tests"] for r in mine)]
        for key, name in (("lint", "new lint"), ("format", "formatting broken")):
            judged = [r for r in mine if r.get(key) is not None]
            if judged:
                parts.append("%s %2d of %d" % (name, sum(bool(r[key]) for r in judged), len(judged)))
        print("  " + " | ".join(parts))


def main(out, *triples):
    scratch, everything = tempfile.mkdtemp(prefix="rules-"), {}
    try:
        for triple in triples:
            clones, language, work = triple.split(":")
            tasks = json.load(open(os.path.join(work, "tasks.json")))
            rows = []
            for task in tasks["tasks"] if isinstance(tasks, dict) else tasks:
                for directory in sorted(glob.glob(os.path.join(work, "runs", task["id"], "*"))):
                    if os.path.isfile(os.path.join(directory, "score.json")) and os.path.isdir(os.path.join(directory, "repo", ".git")):
                        rows.append(one(directory, task, os.path.join(clones, task["repo"]), language, scratch))
            everything[os.path.basename(os.path.normpath(work))] = rows
            report("%s (%s)" % (os.path.basename(os.path.normpath(work)), language), rows)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    with open(out, "w") as handle:
        json.dump(everything, handle, indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])

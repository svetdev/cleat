#!/usr/bin/env python3
"""history — cleat's gates over a repository's own merged commits, no agent and no tokens.

  history.py <clone> <work dir> [commits] [jobs] [newest commit]

For each of the last `commits` first-parent commits that change a source file: the parent's tree,
cleat attached to it so the baselines are the parent's debt, the commit's files laid on, and the
gates run as the Stop hook runs them (`gate.py --changed`). The project's own tests are left out
(`attach.py --no-tests`): they would need an install per commit, and a suite that builds or
needs the network would run at every one. A commit the maintainers merged and a gate refuses is a
candidate false refusal, or debt a person chose to take; the output keeps the gate's own lines so
a sample can be read and told apart.
`newest commit` (default HEAD) starts the stretch further back, at a commit old enough for
later fixes to have landed on it (gates/later_fixes.py). STUDY_SHAS names a file of commits
to replay instead, one per line (gates/eras.py picks them).
Writes <work dir>/<repo>.json: one row per commit with the gates that failed.
"""

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLEAT = os.path.dirname(os.path.dirname(HERE))
SOURCE = (".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".swift", ".kt", ".java", ".rb")


def git(root, *args, **kw):
    return subprocess.run(["git", "-C", root, "-c", "user.name=s", "-c", "user.email=s@example.invalid", *args],
                          capture_output=True, text=True, errors="replace", **kw)


def env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    return env


def commits(clone, count, newest="HEAD"):
    """The newest `count` first-parent, single-parent commits that change a source file: (sha, parent, files)."""
    out = []
    listed = os.environ.get("STUDY_SHAS")
    shas = open(listed).read().split() if listed else git(clone, "rev-list", "--first-parent", "--no-merges", "-n", str(count * 4), newest).stdout.split()
    for sha in shas:
        files = [line.split("\t") for line in git(clone, "diff", "--name-status", "--no-renames", sha + "^", sha).stdout.splitlines()]
        if any(f[-1].endswith(SOURCE) for f in files if len(f) > 1):
            out.append((sha, files))
        if len(out) == count:
            break
    return out


def one(clone, work, sha, files):
    root = os.path.join(work, "tmp", sha[:12])
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    try:
        archive = subprocess.Popen(["git", "-C", clone, "archive", sha + "^"], stdout=subprocess.PIPE)
        subprocess.run(["tar", "-x", "-C", root], stdin=archive.stdout, check=True)
        archive.wait()
        git(root, "init", "-q")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "base")
        attach = subprocess.run(["python3", os.path.join(CLEAT, "quality", "bin", "attach.py"), "--no-tests", "--into", root],
                                capture_output=True, text=True, env=env())
        if attach.returncode != 0:
            return dict(sha=sha, error="attach: " + (attach.stdout + attach.stderr)[-300:])
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "attach")
        for status, path in [(f[0], f[-1]) for f in files if len(f) > 1]:
            target = os.path.join(root, path)
            if status.startswith("D"):
                if os.path.lexists(target):
                    os.remove(target)
                continue
            blob = subprocess.run(["git", "-C", clone, "show", "%s:%s" % (sha, path)], capture_output=True)
            if blob.returncode == 0:   # a submodule or a symlink to nowhere is left as it was
                os.makedirs(os.path.dirname(target) or root, exist_ok=True)
                with open(target, "wb") as handle:
                    handle.write(blob.stdout)
        gate = subprocess.run(["python3", "quality/bin/gate.py", "--changed"], cwd=root, capture_output=True, text=True, env=env())
        said = gate.stdout + gate.stderr
        failing = sorted({line.split()[1] for line in said.splitlines() if line.split()[:1] == ["FAIL"] and len(line.split()) > 1})
        stat = git(clone, "diff", "--shortstat", sha + "^", sha).stdout.strip()   # the commit's own size, new files included
        return dict(sha=sha, exit=gate.returncode, failing=failing, files=[f[-1] for f in files if len(f) > 1][:40],
                    stat=stat, said=said[-4000:] if failing else "")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main(clone, work, count="100", jobs="6", newest="HEAD"):
    clone, work = os.path.abspath(clone), os.path.abspath(work)
    if os.path.realpath(work).startswith(os.path.realpath(CLEAT) + os.sep):
        raise SystemExit("work dir %s is inside the cleat repository" % work)
    os.makedirs(work, exist_ok=True)
    todo = commits(clone, int(count), newest)
    with concurrent.futures.ThreadPoolExecutor(int(jobs)) as pool:
        rows = list(pool.map(lambda c: one(clone, work, *c), todo))
    name = os.path.basename(clone.rstrip("/"))
    with open(os.path.join(work, name + ".json"), "w") as handle:
        json.dump(rows, handle, indent=1)
    refused = [r for r in rows if r.get("failing")]
    gates = {}
    for r in refused:
        for g in r["failing"]:
            gates[g] = gates.get(g, 0) + 1
    print(name, len(rows), "commits,", len(refused), "refused,", sum("error" in r for r in rows), "errors", gates, flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])

#!/usr/bin/env python3
"""gate_variants — what cleat's real gates say about meaning-preserving edits, per repository.

For each sampled clone: attach a given cleat to a copy of the original sample and
commit it, lay a variant (rename, reorder, unparse, black) over it uncommitted,
and run check-complexity, check-escapes and check-duplication as an agent's
change would meet them. Records each gate's exit and its new/worse counts.

  STUDY_WORK=… gate_variants.py <cleat dir> <label> [variant …]

Writes <STUDY_WORK>/q3/data/gate-variants-<label>.json.
"""

import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common   # noqa: E402

GATES = ("check-complexity.py", "check-escapes.py", "check-duplication.py")
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", CLEAT_HOOKS="off")
NEW_RE = re.compile(r"FAIL: (\d+) new ")
WORSE_RE = re.compile(r"FAIL: (\d+) baselined ")
TWIN_RE = re.compile(r"FAIL: (\d+) clone pair")


def sh(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=ENV)


def commit(d, message):
    sh(["git", "add", "-A"], d)
    sh(["git", "-c", "user.email=q@example.invalid", "-c", "user.name=q", "commit", "-qm", message], d)


def judge(cleat, clone, variant, label):
    d = os.path.join(common.Q3, "tmp", "gate-variants-" + label, clone)
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(os.path.join(common.DATA, "orig", clone), d)
    sh(["git", "init", "-q", "-b", "main"], d)
    commit(d, "orig")
    sh([sys.executable, os.path.join(cleat, "quality", "bin", "attach.py"), "--into", "."], d)
    commit(d, "attach")
    source = os.path.join(common.DATA, variant, clone)
    for dirpath, _, files in os.walk(source):
        for name in files:
            path = os.path.join(dirpath, name)
            shutil.copy(path, os.path.join(d, os.path.relpath(path, source)))
    row = {}
    for gate in GATES:
        p = sh([sys.executable, os.path.join("quality", "bin", gate)], d)
        out = p.stdout + p.stderr
        row[gate] = dict(rc=p.returncode, new=sum(int(x) for x in NEW_RE.findall(out)),
                         worse=sum(int(x) for x in WORSE_RE.findall(out)), twins=sum(int(x) for x in TWIN_RE.findall(out)))
    shutil.rmtree(d, ignore_errors=True)
    return row


def main(cleat, label, *variants):
    clones = sorted(os.listdir(os.path.join(common.DATA, "orig")))
    result = {}
    for variant in variants or ("rename", "reorder", "unparse", "black"):
        for clone in clones:
            result["%s/%s" % (variant, clone)] = judge(os.path.abspath(cleat), clone, variant, label)
            print(variant, clone, {g: r["rc"] for g, r in result["%s/%s" % (variant, clone)].items()}, flush=True)
    with open(os.path.join(common.DATA, "gate-variants-%s.json" % label), "w") as handle:
        json.dump(result, handle, indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])

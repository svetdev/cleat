#!/usr/bin/env python3
"""verify_gate — the in-process ratchet reading of analyze_q1 checked against the real gates:
attach cleat to a copy of a clone's orig sample (baselines written by attach), overwrite the
files with a variant, run the project's own check-complexity.py, check-escapes.py and
check-duplication.py, and compare their failure counts with analyze_q1's per-clone numbers.
Writes data/verify-gate.json."""
import json, os, pickle, re, shutil, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common, analyze_q1 as A

CLONES = sys.argv[1:] or ["pallets__click", "psf__requests", "jd__tenacity", "encode__httpx"]
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", CLEAT_HOOKS="off")


def sh(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=ENV)


def count(out, word):
    m = re.search(r"FAIL: (\d+) (?:new )?", out)
    return out


def main():
    orig = A.load("orig")
    res = {}
    for variant in ("rename", "black", "unparse"):
        var = A.load(variant)
        for c in CLONES:
            d = os.path.join(common.Q3, "tmp", "verify", c)
            if os.path.exists(d):
                shutil.rmtree(d)
            shutil.copytree(os.path.join(common.DATA, "orig", c), d)
            sh(["git", "init", "-q", "-b", "main"], d)
            sh(["git", "add", "-A"], d)
            sh(["git", "-c", "user.email=q@x", "-c", "user.name=q", "commit", "-qm", "orig"], d)
            sh([sys.executable, os.path.join(common.CLEAT, "quality/bin/attach.py"), "--into", "."], d)
            sh(["git", "add", "-A"], d)
            sh(["git", "-c", "user.email=q@x", "-c", "user.name=q", "commit", "-qm", "attach"], d)
            for dp, _, fs in os.walk(os.path.join(common.DATA, variant, c)):
                for f in fs:
                    src = os.path.join(dp, f)
                    shutil.copy(src, os.path.join(d, os.path.relpath(src, os.path.join(common.DATA, variant, c))))
            row = {}
            for gate in ("check-complexity.py", "check-escapes.py", "check-duplication.py"):
                p = sh([sys.executable, "quality/bin/" + gate], d)
                out = p.stdout + p.stderr
                row[gate] = {"rc": p.returncode, "head": [l for l in out.split("\n") if l.startswith(("FAIL", "OK", "NOTE"))][:4]}
            km = A.keymap(variant)
            o1 = {c: orig[c]}
            v1 = {c: var[c]}
            row["in_process"] = {"complexity": {k: v for k, v in A.complexity_l2(o1, v1).items() if k in ("new", "worsened", "clones_gate_fails")},
                                 "escapes": {k: v for k, v in A.escapes(o1, v1, km)["gate"].items() if k in ("new", "worsened", "clones_gate_fails")}}
            res["%s/%s" % (variant, c)] = row
            shutil.rmtree(d)
            print(variant, c, row["check-complexity.py"]["rc"], row["in_process"]["complexity"]["clones_gate_fails"]["n"],
                  row["check-escapes.py"]["rc"], row["in_process"]["escapes"]["clones_gate_fails"]["n"], file=sys.stderr)
    with open(os.path.join(common.DATA, "verify-gate.json"), "w") as h:
        json.dump(res, h, indent=1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""test_semantics — the projects' own test suites (tests/ from the clone at HEAD, via git archive)
run against each variant of the sample, as a check that the transformations preserve meaning.
Clones whose whole production tree is in the sample and whose suites need nothing missing:
pallets/click, jd/tenacity. Writes data/semantics.json."""
import json, os, re, shutil, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
Q3 = os.path.join(os.environ.get("STUDY_WORK", ".study-work"), "q3")
D = os.path.join(Q3, "data")
PY = os.path.join(Q3, "..", "venv", "bin", "python")
PKGS = {"pallets__click": "src", "jd__tenacity": "."}


def main():
    out = {}
    for c, srcdir in PKGS.items():
        for v in ("orig", "rename", "reorder", "unparse", "black", "extract1", "extractN"):
            w = os.path.join(Q3, "tmp", "sem", c)
            if os.path.exists(w):
                shutil.rmtree(w)
            shutil.copytree(os.path.join(D, "orig", c), w)
            vroot = os.path.join(D, v, c)
            for dp, _, fs in os.walk(vroot):
                for f in (f for f in fs if f.endswith(".py")):
                    shutil.copy(os.path.join(dp, f), os.path.join(w, os.path.relpath(os.path.join(dp, f), vroot)))
            shutil.copytree(os.path.join(Q3, "tmp", "tests", c, "tests"), os.path.join(w, "tests"))
            shutil.copy(os.path.join(Q3, "tmp", "tests", c, "pyproject.toml"), w)
            env = dict(os.environ, PYTHONPATH=os.path.join(w, srcdir), PYTHONDONTWRITEBYTECODE="1")
            p = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], cwd=w, capture_output=True, text=True, env=env, timeout=900)
            tail = p.stdout.strip().split("\n")[-1]
            counts = {k: int(n) for n, k in re.findall(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)", tail)}
            out["%s/%s" % (c, v)] = {"rc": p.returncode, "summary": tail, **counts}
            print(c, v, tail, file=sys.stderr)
            shutil.rmtree(w)
    json.dump(out, open(os.path.join(D, "semantics.json"), "w"), indent=1)


if __name__ == "__main__":
    main()

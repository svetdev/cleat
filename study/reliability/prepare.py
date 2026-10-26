#!/usr/bin/env python3
"""prepare — extract a seeded random sample of production .py files from each bare clone
(read-only: `git archive HEAD`, never fetch or write into the clone) into q3/data/orig/<clone>/.

Production = not under a tests/test/testing/docs/doc/examples/benchmarks/scripts directory,
not test_*.py / *_test.py / conftest.py, and parses with ast under Python 3.14.
Writes q3/data/manifest.json: clone, commit, files sampled, files eligible.
"""
import ast, json, os, random, re, subprocess, sys, shutil

S = os.environ.get("STUDY_WORK", ".study-work")   # scratch: clones, trees, measurements
CLONES = os.path.join(S, "work/clones")
Q3 = os.path.join(S, "q3")
OUT = os.path.join(Q3, "data/orig")
PER_CLONE = int(os.environ.get("PER_CLONE", "80"))
SEED = 20261024
NONPROD = re.compile(r"(^|/)(tests?|testing|docs?|examples?|benchmarks?|scripts|tools|test_data|testdata|fixtures|vendor|_vendor|node_modules|\.github)(/|$)")
NONPROD_FILE = re.compile(r"(^|/)(test_[^/]*|[^/]*_test|conftest)\.py$")


def main():
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    manifest = []
    rng = random.Random(SEED)
    for clone in sorted(os.listdir(CLONES)):
        git = os.path.join(CLONES, clone)
        commit = subprocess.check_output(["git", "-C", git, "rev-parse", "HEAD"], text=True).strip()
        names = subprocess.check_output(["git", "-C", git, "ls-tree", "-r", "--name-only", "HEAD"], text=True).split("\n")
        cands = sorted(n for n in names if n.endswith(".py") and not NONPROD.search(n) and not NONPROD_FILE.search(n))
        dest = os.path.join(OUT, clone)
        os.makedirs(dest)
        # extract all candidates (small: .py only), then keep a parseable sample
        if not cands:
            continue
        archive = subprocess.run(["git", "-C", git, "archive", "HEAD", "--"] + cands, capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", dest], input=archive.stdout, check=True)
        ok = []
        for n in cands:
            p = os.path.join(dest, n)
            try:
                with open(p, encoding="utf-8") as h:
                    ast.parse(h.read())
                ok.append(n)
            except Exception:
                pass
        sample = sorted(rng.sample(ok, min(PER_CLONE, len(ok))))
        keep = set(sample)
        for n in cands:
            if n not in keep:
                if os.path.exists(os.path.join(dest, n)): os.unlink(os.path.join(dest, n))
        for d, dirs, files in os.walk(dest, topdown=False):
            if not os.listdir(d):
                os.rmdir(d)
        manifest.append({"clone": clone, "commit": commit, "eligible": len(ok), "sampled": len(sample), "files": sample})
        print(clone, len(ok), len(sample), file=sys.stderr)
    with open(os.path.join(Q3, "data/manifest.json"), "w") as h:
        json.dump(manifest, h, indent=1)


if __name__ == "__main__":
    main()

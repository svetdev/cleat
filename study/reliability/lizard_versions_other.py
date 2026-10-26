#!/usr/bin/env python3
"""lizard_versions_other — lizard 1.24.0 vs 1.24.1 through cleat's extractor on the Swift
(Alamofire, Nuke) and TypeScript (zod) trees used for instrument agreement. Python is in
analyze_q1 (data/q1.json, "lizard_1.24.0_vs_1.24.1"). Writes data/lizard-versions-other.json."""
import json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
Q3 = os.path.join(os.environ.get("STUDY_WORK", ".study-work"), "q3")

CHILD = r'''
import json, os, sys
sys.path.insert(0, %r)
import common
spec = {"tool": "lizard", "languages": [sys.argv[1]], "exclude": []}
roots = sys.argv[2:]
fns, _, _, v = common.complexity.measure(spec, roots, [], root=os.path.commonpath(roots))
print(json.dumps({"version": v, "f": [[f.path, f.line, f.cc, f.length] for f in fns]}))
''' % HERE


def run(bin_dir, lang, roots):
    env = dict(os.environ, PATH=bin_dir + os.pathsep + os.environ["PATH"])
    p = subprocess.run([os.path.join(Q3, "..", "venv", "bin", "python"), "-c", CHILD, lang, *roots], capture_output=True, text=True, env=env, check=True)
    return json.loads(p.stdout)


def main():
    old_bin = os.path.join(Q3, "venv1240", "bin")
    new_bin = "/opt/homebrew/bin"
    sets = {"swift": [os.path.join(Q3, "tmp/alamofire/Source"), os.path.join(Q3, "tmp/nuke/Sources")],
            "typescript": [os.path.join(Q3, "node/tsrc")]}
    out = {}
    for lang, roots in sets.items():
        a, b = run(old_bin, lang, roots), run(new_bin, lang, roots)
        A = {(p, l): (c, n) for p, l, c, n in a["f"]}
        B = {(p, l): (c, n) for p, l, c, n in b["f"]}
        common_keys = set(A) & set(B)
        over = lambda v: v[0] > 8 or v[1] > 60
        out[lang] = {"versions": [a["version"], b["version"]], "functions_old": len(A), "functions_new": len(B),
                     "matched": len(common_keys), "identical": sum(A[k] == B[k] for k in common_keys),
                     "verdict_same": sum(over(A[k]) == over(B[k]) for k in common_keys),
                     "over_old": sum(over(v) for v in A.values()), "over_new": sum(over(v) for v in B.values()),
                     "examples": [[os.path.basename(k[0]), k[1], A[k], B[k]] for k in sorted(common_keys) if A[k] != B[k]][:10]}
    with open(os.path.join(Q3, "data", "lizard-versions-other.json"), "w") as h:
        json.dump(out, h, indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

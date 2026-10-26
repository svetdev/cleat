#!/usr/bin/env python3
"""measure — run cleat's extractors (complexity via lizard with the attach config, the python
escape patterns, the built-in duplication finder) over each variant tree, per clone.

  measure.py VARIANT [--lizard-bin DIR] [--as NAME]

Writes data/meas/NAME.pkl: {clone: {"cx": {(rel,key):(cc,len,line)}, "cx_findings": [entry],
"lizard": version, "esc": [(rel,line,text,kind)], "dup": [(locations, lines)], "density": (d,t),
"index": {rel: def_index}}}.
"""
import argparse, os, pickle, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("variant")
    ap.add_argument("--lizard-bin")
    ap.add_argument("--as", dest="name")
    ap.add_argument("--only-cx", action="store_true")
    a = ap.parse_args()
    if a.lizard_bin:
        os.environ["PATH"] = a.lizard_bin + os.pathsep + os.environ["PATH"]
    out = {}
    for c in common.clones(a.variant):
        root = os.path.join(common.DATA, a.variant, c)
        cx, version, findings = common.measure_complexity(root)
        rec = {"cx": cx, "lizard": version, "index": common.index_tree(root), "cx_findings": findings}
        if not a.only_cx:
            rec["esc"] = common.escape_sites(root)
            cl, dens = common.dup_measure(root)
            rec["dup"] = [(c_.locations, c_.lines) for c_ in cl]
            rec["density"] = dens
        out[c] = rec
        print(a.variant, c, len(cx), version, file=sys.stderr)
    os.makedirs(os.path.join(common.DATA, "meas"), exist_ok=True)
    with open(os.path.join(common.DATA, "meas", (a.name or a.variant) + ".pkl"), "wb") as h:
        pickle.dump(out, h)


if __name__ == "__main__":
    main()

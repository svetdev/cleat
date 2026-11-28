#!/usr/bin/env python3
"""drift — do the arms of a replay move apart as a chain goes on?

  drift.py [replay work root]        (default ~/cleat-study/replay)

Reads the chain steps chain.py wrote, starts no agent. Per project and stretch of the chain,
per arm: steps, median and mean cost of a step (every attempt counted), scored steps whose own
tests and whose full check pass, steps retried and rejected. Per stretch, the median over its
steps of cleat's mean cost on the step over plain's on the same step. If simpler code made
the next change cheaper, that ratio would fall along the chain and cleat's resolved share rise.
"""
import json, os, glob, statistics, sys, collections
ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.expanduser("~/cleat-study/replay")
PROJ = {"zod": (150, 30), "zod-accept": (90, 30), "just": (90, 30), "starship": (80, 20), "zustand": (80, 20)}
def cost(s):
    if s.get("attempts"):
        return sum((a.get("run") or {}).get("total_cost_usd") or 0 for a in s["attempts"])
    return (s.get("run") or {}).get("total_cost_usd") or 0
for p, (upto, seg) in PROJ.items():
    print("==", p)
    rows = collections.defaultdict(dict)   # (arm) -> step -> (cost, scored, own, full, retried, rejected)
    for chain in sorted(os.listdir(os.path.join(ROOT, p, "chains"))):
        arm = chain.split("-")[0]
        if arm == "oracle": continue
        for d in glob.glob(os.path.join(ROOT, p, "chains", chain, "steps", "*")):
            f = os.path.join(d, "score.json")
            if not os.path.isfile(f): continue
            n = int(os.path.basename(d))
            if n > upto: continue
            s = json.load(open(f))
            rows[chain][n] = dict(cost=cost(s), scored="check" in s and bool(s["check"]),
                own=bool((s.get("check") or {}).get("resolved")),
                full=bool((s.get("check_full") or {}).get("resolved")) and s.get("accepted", True) is not False,
                retried=len(s.get("attempts") or []) > 1, rejected=s.get("accepted") is False,
                turns=(s.get("run") or {}).get("num_turns") or 0)
    for lo in range(1, upto + 1, seg):
        hi = lo + seg - 1
        out = []
        per = {}
        for arm in ("plain", "cleat"):
            rs = [r for c, steps in rows.items() if c.startswith(arm) for n, r in steps.items() if lo <= n <= hi]
            sc = [r for r in rs if r["scored"]]
            per[arm] = rs
            out.append("%s n=%3d med $%.2f mean $%.2f own %2d/%2d full %2d/%2d retried %d rejected %d" % (
                arm, len(rs), statistics.median(r["cost"] for r in rs) if rs else 0, statistics.mean(r["cost"] for r in rs) if rs else 0,
                sum(r["own"] for r in sc), len(sc), sum(r["full"] for r in sc), len(sc), sum(r["retried"] for r in rs), sum(r["rejected"] for r in rs)))
        # paired by step: ratio of mean cleat cost to mean plain cost on the same step
        ratios = []
        for n in range(lo, hi + 1):
            pc = [steps[n]["cost"] for c, steps in rows.items() if c.startswith("plain") and n in steps and steps[n]["cost"]]
            cc = [steps[n]["cost"] for c, steps in rows.items() if c.startswith("cleat") and n in steps and steps[n]["cost"]]
            if pc and cc:
                ratios.append(statistics.mean(cc) / statistics.mean(pc))
        med = statistics.median(ratios) if ratios else float("nan")
        print("  %3d-%3d  cleat/plain per-step cost, median ratio %.2f (%d steps)" % (lo, hi, med, len(ratios)))
        for o in out: print("      " + o)

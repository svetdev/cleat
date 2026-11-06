#!/usr/bin/env python3
"""analyze — the replay's numbers against the 2026-11-03 pre-registration's predictions.

  analyze.py <clones dir> <work dir> <project dir …>       (project dirs relative to the work dir)

Per project and pooled by arm: functions over cyclomatic 8 per 1,000 production lines at
changes 40 and 80 (pooled as the sum of functions over 8 over the sum of lines), the same
for upstream's tree after the same change, the share of scored changes resolved, the median
cost of a change, and the probes' median cost and resolved count per kind of tree; resolution
by the full check (every upstream test file laid over) is shown beside the pre-registered one. Upstream's
measures after every change are cached in <project>/upstream.jsonl. Prints a report and
writes <work>/analysis.json.
"""

import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import measure   # noqa: E402

POINTS = (40, 80)


def read(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def upstream(clones, project, plan):
    """{change index: measure} of upstream's tree after each change of the stretch."""
    path = os.path.join(project, "upstream.jsonl")
    got = {}
    if os.path.exists(path):
        for line in open(path):
            row = json.loads(line)
            got[row["index"]] = row["measure"]
    with open(path, "a") as handle:
        for change in plan["changes"][:max(POINTS)]:
            if change["index"] not in got:
                got[change["index"]] = measure.upstream(plan["lang"], os.path.join(clones, plan["repo"]), change["sha"])
                handle.write(json.dumps(dict(index=change["index"], measure=got[change["index"]])) + "\n")
    return got


def chains(project):
    """{chain: {change index: step score}}."""
    out = {}
    base = os.path.join(project, "chains")
    for chain in sorted(os.listdir(base)):
        steps = os.path.join(base, chain, "steps")
        if os.path.isdir(steps):
            out[chain] = {int(n): read(os.path.join(steps, n, "score.json")) for n in sorted(os.listdir(steps))}
            out[chain] = {n: s for n, s in out[chain].items() if s}
    return out


def probes(project, point):
    """[(tree kind, probe score)] at a checkpoint: tree kind is plain, cleat or upstream."""
    base = os.path.join(project, "probes", str(point))
    out = []
    if not os.path.isdir(base):
        return out
    for tree in sorted(os.listdir(base)):
        for run in sorted(os.listdir(os.path.join(base, tree))):
            s = read(os.path.join(base, tree, run, "score.json"))
            if s:
                out.append((tree.lstrip("_").split("-")[0], s))
    return out


def density(measures):
    lines = sum(m["lines"] for m in measures)
    return 1000.0 * sum(m["over8"] for m in measures) / lines if lines else None


def cost(score):
    return (score.get("run") or {}).get("total_cost_usd")


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def project_report(clones, project):
    plan = json.load(open(os.path.join(project, "plan.json")))
    up = upstream(clones, project, plan)
    runs = chains(project)
    out = dict(repo=plan["repo"], lang=plan["lang"], scored=sum(c["scored"] for c in plan["changes"][:max(POINTS)]),
               trees={}, upstream={p: up[p] for p in POINTS if p in up}, probes={})
    for chain, steps in runs.items():
        scored = [s["check"] for s in steps.values() if s.get("check")]
        out["trees"][chain] = dict(
            arm=chain.split("-")[0], done=len(steps),
            measure={p: steps[p]["measure"] for p in POINTS if p in steps},
            resolved=sum(c["resolved"] for c in scored), scored=len(scored),
            resolved_full=sum(s["check_full"]["resolved"] for s in steps.values() if s.get("check_full")),
            scored_full=sum(bool(s.get("check_full")) for s in steps.values()),
            missing=sum(c["missing"] > 0 for c in scored), cost=median(cost(s) for s in steps.values()),
            costs=[cost(s) for s in steps.values()])
    for point in POINTS:
        rows = probes(project, point)
        out["probes"][point] = {kind: dict(runs=sum(k == kind for k, _ in rows),
                                           resolved=sum(k == kind and s["check"]["resolved"] for k, s in rows),
                                           resolved_full=sum(k == kind and (s.get("check_full") or {}).get("resolved", False) for k, s in rows),
                                           costs=[cost(s) for k, s in rows if k == kind],
                                           cost=median(cost(s) for k, s in rows if k == kind))
                                for kind in ("plain", "cleat", "upstream")}
    return out


def pooled(projects):
    arms = {}
    for arm in ("plain", "cleat"):
        trees = [t for p in projects for t in p["trees"].values() if t["arm"] == arm]
        arms[arm] = dict(
            density={pt: density([t["measure"][pt] for t in trees if pt in t["measure"]]) for pt in POINTS},
            resolved=sum(t["resolved"] for t in trees), scored=sum(t["scored"] for t in trees),
            resolved_full=sum(t["resolved_full"] for t in trees), scored_full=sum(t["scored_full"] for t in trees),
            cost=median(c for t in trees for c in t["costs"]),
            probes={pt: dict(runs=sum(p["probes"][pt][arm]["runs"] for p in projects),
                             resolved=sum(p["probes"][pt][arm]["resolved"] for p in projects),
                             resolved_full=sum(p["probes"][pt][arm]["resolved_full"] for p in projects),
                             cost=median(c for p in projects for c in p["probes"][pt][arm]["costs"]))
                    for pt in POINTS})
    arms["upstream"] = dict(density={pt: density([p["upstream"][pt] for p in projects if pt in p["upstream"]]) for pt in POINTS})
    return arms


def verdicts(arms):
    plain, cleat, up = arms["plain"], arms["cleat"], arms["upstream"]
    d = lambda a, p: a["density"].get(p)
    ratio = lambda a, b: a / b if a is not None and b else None
    rate = lambda a: 100.0 * a["resolved"] / a["scored"] if a["scored"] else None
    pc = lambda a: a["probes"][80]["cost"]
    checks = [
        ("plain over-8 density at 80 at least 2x cleat's", ratio(d(plain, 80), d(cleat, 80)), lambda r: r >= 2),
        ("plain over-8 density at 80 above upstream's", ratio(d(plain, 80), d(up, 80)), lambda r: r > 1),
        ("cleat density 80/40 at most 1.5", ratio(d(cleat, 80), d(cleat, 40)), lambda r: r <= 1.5),
        ("plain density 80/40 below 1.5", ratio(d(plain, 80), d(plain, 40)), lambda r: r < 1.5),
        ("probe cost at 80, plain/cleat within 20%", ratio(pc(plain), pc(cleat)), lambda r: 1 / 1.2 <= r <= 1.2),
        ("probes resolved at 80, plain minus cleat within 3",
         plain["probes"][80]["resolved"] - cleat["probes"][80]["resolved"]
         if plain["probes"][80]["runs"] and cleat["probes"][80]["runs"] else None, lambda r: abs(r) <= 3),
        ("cleat resolves within 10 points of plain",
         (rate(cleat) - rate(plain)) if rate(cleat) is not None and rate(plain) is not None else None, lambda r: abs(r) <= 10),
        ("cleat change cost 1.3 to 2.2x plain", ratio(cleat["cost"], plain["cost"]), lambda r: 1.3 <= r <= 2.2),
    ]
    return [(name, value, None if value is None else bool(test(value))) for name, value, test in checks]


def fmt(x):
    return "-" if x is None else ("%.2f" % x if isinstance(x, float) else str(x))


def main(clones, work, *projects):
    clones, work = os.path.abspath(clones), os.path.abspath(work)
    reports = [project_report(clones, os.path.join(work, p)) for p in projects]
    for r in reports:
        print("\n%s (%s): %d scored changes in the stretch" % (r["repo"], r["lang"], r["scored"]))
        print("  %-10s %5s %18s %18s %9s %8s %8s" % ("tree", "done", "over8/kloc @40", "over8/kloc @80", "resolved", "missing", "cost") + "  full check")
        for name, t in r["trees"].items():
            m = t["measure"]
            print("  %-10s %5d %18s %18s %9s %8d %8s" % (
                name, t["done"], fmt(m[40]["over8_per_kloc"]) if 40 in m else "-",
                fmt(m[80]["over8_per_kloc"]) + " (%d lines)" % m[80]["lines"] if 80 in m else "-",
                "%d/%d" % (t["resolved"], t["scored"]), t["missing"], fmt(t["cost"])) +
                  "  %d/%d" % (t["resolved_full"], t["scored_full"]))
        u = r["upstream"]
        print("  %-10s %5s %18s %18s" % ("upstream", "", fmt(u[40]["over8_per_kloc"]) if 40 in u else "-",
                                       fmt(u[80]["over8_per_kloc"]) + " (%d lines)" % u[80]["lines"] if 80 in u else "-"))
        for point, kinds in r["probes"].items():
            print("  probes @%d: " % point + "; ".join("%s %d/%d resolved (full %d), median $%s" % (k, v["resolved"], v["runs"], v["resolved_full"], fmt(v["cost"]))
                                                     for k, v in kinds.items() if v["runs"]))
    arms = pooled(reports)
    print("\npooled over-8 per kloc: " + "; ".join("%s @40 %s @80 %s" % (a, fmt(v["density"][40]), fmt(v["density"][80])) for a, v in arms.items()))
    for arm in ("plain", "cleat"):
        a = arms[arm]
        print("%s: resolved %d/%d (full check %d/%d), median change cost $%s, probes @80 %d/%d resolved (full %d)" % (
            arm, a["resolved"], a["scored"], a["resolved_full"], a["scored_full"], fmt(a["cost"]),
            a["probes"][80]["resolved"], a["probes"][80]["runs"], a["probes"][80]["resolved_full"]))
    print("\npredictions:")
    results = verdicts(arms)
    for name, value, held in results:
        print("  %-52s %8s  %s" % (name, fmt(value), {True: "holds", False: "fails", None: "no data"}[held]))
    with open(os.path.join(work, "analysis.json"), "w") as handle:
        json.dump(dict(projects=reports, arms=arms, predictions=results), handle, indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])

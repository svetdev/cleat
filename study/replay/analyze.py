#!/usr/bin/env python3
"""analyze — the replay's numbers against the 2026-11-04 pre-registration's predictions.

  analyze.py <clones dir[,clones dir …]> <work dir> <project dir …>   (project dirs relative to the work dir)

Per project and pooled by arm: functions over cyclomatic 8 per 1,000 production lines after
each checkpoint the plans name (20, 40, 60 and 80 in the 2026-11-04 run) (pooled as the sum of functions over 8 over the sum of lines), the
same for upstream's tree after the same change; the share of scored changes resolved by the
full check (every upstream test file and runner config laid over), the change's own files
alone beside it; the oracle chain's resolution, which gates the rest; the median cost of a
change; anything an agent left that a run must not carry (`stray`); and per checkpoint and
probe mode, each kind of tree's probe runs, resolved count (judged against the tree's own
baseline; the plain full check beside it) and median cost. Upstream's
measures are cached in <project>/upstream.jsonl. Prints a report and writes
<work>/analysis.json. The first run's (2026-11-03) is analyze_first.py.
"""

import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import measure   # noqa: E402

POINTS = (20, 40, 60, 80)   # the plans' checkpoints, set in main
MODES = ("hidden", "visible")
KINDS = ("plain", "cleat", "upstream")


def read(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def ratio(a, b):
    return a / b if a is not None and b else None


def cost(s):
    return (s.get("run") or {}).get("total_cost_usd")


def full(s):
    """Resolved by the full check, and for a chain step accepted: a rejected change kept nothing."""
    return (s.get("check_full") or {}).get("resolved", False) and s.get("accepted", True) is not False


def judged(s):
    """A probe run resolved against its tree's baseline (probe.judged)."""
    return (s.get("judged") or {}).get("resolved", False)


def upstream(clones, project, plan):
    """{change index: measure} of upstream's tree after each checkpoint's change."""
    path = os.path.join(project, "upstream.jsonl")
    got = {}
    if os.path.exists(path):
        for line in open(path):
            row = json.loads(line)
            got[row["index"]] = row["measure"]
    with open(path, "a") as handle:
        for change in plan["changes"]:
            if change["index"] in POINTS and change["index"] not in got:
                got[change["index"]] = measure.upstream(plan["lang"], os.path.join(clones, plan["repo"]), change["sha"])
                handle.write(json.dumps(dict(index=change["index"], measure=got[change["index"]])) + "\n")
    return got


def chains(project, upto):
    """{chain: {change index: step score}} through change `upto`."""
    out = {}
    base = os.path.join(project, "chains")
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        steps = os.path.join(base, name, "steps")
        if os.path.isdir(steps):
            out[name] = {int(n): s for n in sorted(os.listdir(steps)) if int(n) <= upto for s in [read(os.path.join(steps, n, "score.json"))] if s}
    return out


def probes(project):
    """[(checkpoint, mode, tree kind, probe score)]."""
    base = os.path.join(project, "probes")
    out = []
    for point in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        for tree in sorted(os.listdir(os.path.join(base, point))):
            for run in sorted(os.listdir(os.path.join(base, point, tree))):
                s = read(os.path.join(base, point, tree, run, "score.json"))
                if s:
                    out.append((int(point), s.get("mode", "hidden"), tree.lstrip("_").split("-")[0], s))
    return out


def density(measures):
    lines = sum(m["lines"] for m in measures)
    return 1000.0 * sum(m["over8"] for m in measures) / lines if lines else None


def tree_report(steps):
    scored = [s for s in steps.values() if s.get("check")]
    return dict(done=len(steps), measure={p: steps[p]["measure"] for p in POINTS if p in steps},
                scored=len(scored), resolved=sum(full(s) for s in scored),
                resolved_own=sum(s["check"]["resolved"] for s in scored),
                missing=sum(s["check_full"]["missing"] > 0 for s in scored if s.get("check_full")),
                costs=[cost(s) for s in steps.values()],
                stray=sorted({p for s in steps.values() for p in s.get("stray", [])}))


def probe_report(rows):
    out = {}
    for point in POINTS:
        for mode in MODES:
            for kind in KINDS:
                runs = [s for p, m, k, s in rows if (p, m, k) == (point, mode, kind)]
                out["%d/%s/%s" % (point, mode, kind)] = dict(
                    runs=len(runs), resolved=sum(judged(s) for s in runs), resolved_full=sum(full(s) for s in runs),
                    unjudged=sum("judged" not in s for s in runs), costs=[cost(s) for s in runs],
                    cost=median(cost(s) for s in runs))
    return out


def project_report(clones, project, upto):
    plan = json.load(open(os.path.join(project, "plan.json")))
    clones = next(c for c in clones if os.path.isdir(os.path.join(c, plan["repo"])))
    trees = {name: dict(tree_report(steps), arm=name.split("-")[0]) for name, steps in chains(project, upto).items()}
    return dict(repo=plan["repo"], lang=plan["lang"], scored=sum(c["scored"] for c in plan["changes"] if c["index"] <= upto),
                upstream=upstream(clones, project, plan), trees=trees, probes=probe_report(probes(project)))


def pooled(projects):
    arms = {}
    for arm in ("plain", "cleat", "oracle"):
        trees = [t for p in projects for t in p["trees"].values() if t["arm"] == arm]
        arms[arm] = dict(density={pt: density([t["measure"][pt] for t in trees if pt in t["measure"]]) for pt in POINTS},
                         resolved=sum(t["resolved"] for t in trees), resolved_own=sum(t["resolved_own"] for t in trees),
                         scored=sum(t["scored"] for t in trees), cost=median(c for t in trees for c in t["costs"]),
                         stray=sorted({s for t in trees for s in t["stray"]}))
    arms["upstream"] = dict(density={pt: density([p["upstream"][pt] for p in projects if pt in p["upstream"]]) for pt in POINTS})
    arms["probes"] = {}
    for key in projects[0]["probes"] if projects else []:
        cells = [p["probes"][key] for p in projects]
        arms["probes"][key] = dict(runs=sum(c["runs"] for c in cells), resolved=sum(c["resolved"] for c in cells),
                                   resolved_full=sum(c["resolved_full"] for c in cells), unjudged=sum(c["unjudged"] for c in cells),
                                   cost=median(x for c in cells for x in c["costs"]))
    return arms


def visible(arms, kind, points):
    """(runs, resolved) of a kind of tree's visible probes at the given checkpoints, pooled."""
    cells = [arms["probes"]["%d/visible/%s" % (p, kind)] for p in points]
    return sum(c["runs"] for c in cells), sum(c["resolved"] for c in cells)


def visible_cost(projects, kind, points):
    return median(x for p in projects for pt in points for x in p["probes"]["%d/visible/%s" % (pt, kind)]["costs"])


def per_project_gap(projects):
    """The smallest plain-over-cleat density ratio at the last checkpoint across projects (inf where cleat has none)."""
    gaps = []
    for p in projects:
        arm = lambda name: density([t["measure"][POINTS[-1]] for t in p["trees"].values() if t["arm"] == name and POINTS[-1] in t["measure"]])
        plain, cleat = arm("plain"), arm("cleat")
        if plain is None or cleat is None:
            return None
        gaps.append(plain / cleat if cleat else (float("inf") if plain else 1.0))
    return min(gaps) if gaps else None


def verdicts(projects, arms):
    plain, cleat, up = arms["plain"], arms["cleat"], arms["upstream"]
    d = lambda a, p: a["density"].get(p)
    rate = lambda a: 100.0 * a["resolved"] / a["scored"] if a["scored"] else None
    first, last, late = POINTS[0], POINTS[-1], POINTS[-2:]
    oracle = visible(arms, "upstream", POINTS)
    pr, cr = visible(arms, "plain", late), visible(arms, "cleat", late)
    slope = ratio(ratio(visible_cost(projects, "plain", (last,)), visible_cost(projects, "cleat", (last,))),
                  ratio(visible_cost(projects, "plain", (first,)), visible_cost(projects, "cleat", (first,))))
    at, span, both = "at %d" % last, "%d/%d" % (last, first), "%d+%d" % late
    checks = [
        ("gate: oracle resolves >= 90% of scored changes", rate(arms["oracle"]), lambda r: r >= 90),
        ("gate: oracle's visible probes resolve >= 80%", 100.0 * oracle[1] / oracle[0] if oracle[0] else None, lambda r: r >= 80),
        ("plain over-8 density %s at least 2x cleat's" % at, ratio(d(plain, last), d(cleat, last)), lambda r: r >= 2),
        ("plain over-8 density %s above upstream's" % at, ratio(d(plain, last), d(up, last)), lambda r: r > 1),
        ("plain above cleat %s in every project" % at, per_project_gap(projects), lambda r: r > 1),
        ("cleat density %s at most 1.5" % span, ratio(d(cleat, last), d(cleat, first)), lambda r: r <= 1.5),
        ("plain density %s at most 1.5" % span, ratio(d(plain, last), d(plain, first)), lambda r: r <= 1.5),
        ("visible probe cost at %s, plain/cleat within 20%%" % both,
         ratio(visible_cost(projects, "plain", late), visible_cost(projects, "cleat", late)), lambda r: 1 / 1.2 <= r <= 1.2),
        ("visible probes resolved at %s, plain minus cleat within %d of %d" % (both, round(pr[0] / 16), pr[0]),
         pr[1] - cr[1] if pr[0] and cr[0] else None, lambda r: abs(r) <= round(pr[0] / 16)),
        ("plain/cleat visible cost ratio, %d over %d, at most 1.2" % (last, first), slope, lambda r: r <= 1.2),
        ("cleat resolves within 10 points of plain (full check)",
         rate(cleat) - rate(plain) if rate(cleat) is not None and rate(plain) is not None else None, lambda r: abs(r) <= 10),
        ("cleat change cost 1.0 to 1.5x plain", ratio(cleat["cost"], plain["cost"]), lambda r: 1.0 <= r <= 1.5),
    ]
    return [(name, value, None if value is None else bool(test(value))) for name, value, test in checks]


def fmt(x):
    return "-" if x is None else ("%.2f" % x if isinstance(x, float) else str(x))


def print_project(r):
    print("\n%s (%s): %d scored changes" % (r["repo"], r["lang"], r["scored"]))
    print("  %-9s %5s  %s  %9s %9s %8s %6s  %s" % ("tree", "done", "  ".join("over8@%-3d" % p for p in POINTS),
                                                 "resolved", "own files", "missing", "cost", "stray"))
    for name, t in r["trees"].items():
        m = t["measure"]
        print("  %-9s %5d  %s  %9s %9s %8d %6s  %s" % (
            name, t["done"], "  ".join("%8s" % (fmt(m[p]["over8_per_kloc"]) if p in m else "-") for p in POINTS),
            "%d/%d" % (t["resolved"], t["scored"]), "%d/%d" % (t["resolved_own"], t["scored"]), t["missing"],
            fmt(median(t["costs"])), ",".join(t["stray"]) or "-"))
    u = r["upstream"]
    print("  %-9s %5s  %s" % ("upstream", "", "  ".join("%8s" % (fmt(u[p]["over8_per_kloc"]) if p in u else "-") for p in POINTS)))
    for point in POINTS:
        for mode in MODES:
            cells = [(k, r["probes"]["%d/%s/%s" % (point, mode, k)]) for k in KINDS]
            if any(c["runs"] for _, c in cells):
                print("  probes @%d %-7s " % (point, mode) + "; ".join(
                    "%s %d/%d (full %d), $%s" % (k, c["resolved"], c["runs"], c["resolved_full"], fmt(c["cost"]))
                    for k, c in cells if c["runs"]) + ("; %d unjudged" % sum(c["unjudged"] for _, c in cells) if any(c["unjudged"] for _, c in cells) else ""))


def main(clones, work, *projects):
    """A project given as `name@N` is read only through change N, as are the others with it."""
    global POINTS
    clones, work = [os.path.abspath(c) for c in clones.split(",")], os.path.abspath(work)
    upto = min([int(p.split("@")[1]) for p in projects if "@" in p] or [10 ** 9])
    names = [p.split("@")[0] for p in projects]
    POINTS = tuple(sorted(int(k) for k in json.load(open(os.path.join(work, names[0], "plan.json")))["probes"] if int(k) <= upto))
    reports = [project_report(clones, os.path.join(work, p), upto) for p in names]
    for r in reports:
        print_project(r)
    arms = pooled(reports)
    print("\npooled over-8 per kloc @%s: " % "/".join(map(str, POINTS)) + "; ".join(
        "%s %s" % (a, " ".join(fmt(arms[a]["density"][p]) for p in POINTS)) for a in ("plain", "cleat", "oracle", "upstream")))
    for arm in ("oracle", "plain", "cleat"):
        a = arms[arm]
        print("%s: resolved %d/%d (own files %d), median change cost $%s, stray %s" % (
            arm, a["resolved"], a["scored"], a["resolved_own"], fmt(a["cost"]), ",".join(a["stray"]) or "none"))
    print("\npredictions:")
    results = verdicts(reports, arms)
    for name, value, held in results:
        print("  %-60s %8s  %s" % (name, fmt(value), {True: "holds", False: "fails", None: "no data"}[held]))
    with open(os.path.join(work, "analysis-%s.json" % "-".join(projects)), "w") as handle:
        json.dump(dict(projects=reports, arms=arms, predictions=results), handle, indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])

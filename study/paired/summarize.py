#!/usr/bin/env python3
"""summarize — the paired runs against their pre-registered predictions: one run per arm
(2026-10-25), or repeated runs, arms named plain-1, cleat-1, … (2026-10-26).

  summarize.py <work dir>
"""

import glob
import json
import os
import statistics
import sys

VERDICTS = ("complexity", "escapes", "duplication")


def load(work):
    pairs = {}
    for path in sorted(glob.glob(os.path.join(work, "runs", "*", "*", "score.json"))):
        task, arm = path.split(os.sep)[-3:-1]
        with open(path) as handle:
            pairs.setdefault(task, {})[arm] = json.load(handle)["score"]
    return {t: p for t, p in pairs.items() if any(a.startswith("plain") for a in p) and any(a.startswith("cleat") for a in p)}


def row(s):
    mined = s["mined"]
    return dict(resolved=s["resolved"], failing=[v for v in VERDICTS if mined[v]], gate=s["real_gate"]["failing"],
                max_cc=mined["max_cc_changed"], added=mined["functions_added"], la=mined["la"], ld=mined["ld"],
                turns=s["run"].get("num_turns"), cost=s["run"].get("total_cost_usd") or 0.0)


def main(work):
    pairs = load(work)
    rows = {t: {arm: row(s) for arm, s in p.items()} for t, p in pairs.items()}
    print("%-44s %-6s %-26s %-26s %-9s %-9s %-11s %s" % ("task", "", "verdicts failing", "real gate failing", "max cc", "fn added", "turns", "cost"))
    for t, r in rows.items():
        for arm in ("plain", "cleat"):
            x = r[arm]
            print("%-44s %-6s %-26s %-26s %-9s %-9s %-11s %.2f%s" % (t if arm == "plain" else "", arm, ",".join(x["failing"]) or "-",
                  ",".join(x["gate"]) or "-", x["max_cc"], x["added"], x["turns"], x["cost"], "" if x["resolved"] else "  UNRESOLVED"))
    n = len(rows)
    resolved = {arm: sum(r[arm]["resolved"] for r in rows.values()) for arm in ("plain", "cleat")}
    failing = {arm: sum(bool(r[arm]["failing"]) for r in rows.values()) for arm in ("plain", "cleat")}
    gate = {arm: sum(bool(r[arm]["gate"]) for r in rows.values()) for arm in ("plain", "cleat")}
    differ = [r for r in rows.values() if r["plain"]["added"] != r["cleat"]["added"]]
    more = sum(r["cleat"]["added"] > r["plain"]["added"] for r in differ)
    ratios = [r["cleat"]["cost"] / r["plain"]["cost"] - 1 for r in rows.values() if r["plain"]["cost"]]
    total = sum(r["cleat"]["cost"] for r in rows.values()) / sum(r["plain"]["cost"] for r in rows.values()) - 1
    checks = [
        ("arms resolve within 1 task", "%d vs %d" % (resolved["plain"], resolved["cleat"]), abs(resolved["plain"] - resolved["cleat"]) <= 1),
        ("plain ends with a mined verdict failing in >= 3", "%d of %d" % (failing["plain"], n), failing["plain"] >= 3),
        ("cleat ends with a mined verdict failing in <= 1", "%d of %d" % (failing["cleat"], n), failing["cleat"] <= 1),
        ("cleat adds more functions in >= 5 differing pairs", "%d of %d differing" % (more, len(differ)), more >= 5),
        ("cleat costs 20-60% more per task (median)", "%+.0f%% (total %+.0f%%)" % (100 * statistics.median(ratios), 100 * total),
         0.20 <= statistics.median(ratios) <= 0.60),
    ]
    print("\npairs: %d" % n)
    for name, value, held in checks:
        print("  %-52s %-26s %s" % (name, value, "holds" if held else "FALSIFIED"))
    print("  secondary — cleat's real gates failing at the end: plain %d, cleat %d" % (gate["plain"], gate["cleat"]))
    with open(os.path.join(work, "summary.json"), "w") as handle:
        json.dump(dict(rows=rows, checks=[dict(name=c[0], value=c[1], held=c[2]) for c in checks], real_gate=gate), handle, indent=1)


def repeated(work):
    """The 2026-10-26 checks over runs named <arm>-<repeat>."""
    rows = {t: {arm: row(s) for arm, s in p.items()} for t, p in load(work).items()}
    runs = sorted({a for r in rows.values() for a in r})
    repeats = sorted({a.split("-")[1] for a in runs})
    complete = {t: r for t, r in rows.items() if all("%s-%s" % (arm, k) in r for arm in ("plain", "cleat") for k in repeats)}
    n = len(complete)
    resolved = {a: sum(r[a]["resolved"] for r in complete.values()) for a in runs}
    gaps = [abs(resolved["plain-" + k] - resolved["cleat-" + k]) for k in repeats]
    flips = {arm: sum(len({r["%s-%s" % (arm, k)]["resolved"] for k in repeats}) > 1 for r in complete.values()) for arm in ("plain", "cleat")}
    failing = {arm: sum(bool(r["%s-%s" % (arm, k)]["failing"]) for r in complete.values() for k in repeats) for arm in ("plain", "cleat")}
    gate = {arm: sum(bool(r["%s-%s" % (arm, k)]["gate"]) for r in complete.values() for k in repeats) for arm in ("plain", "cleat")}
    mean = lambda r, arm, key: statistics.mean(r["%s-%s" % (arm, k)][key] for k in repeats)
    ratios = [mean(r, "cleat", "cost") / mean(r, "plain", "cost") - 1 for r in complete.values() if mean(r, "plain", "cost")]
    runs_per_arm = n * len(repeats)
    checks = [
        ("arms resolve within 3 tasks per repeat (mean gap)", "%s, mean %.1f" % (resolved, statistics.mean(gaps)), statistics.mean(gaps) <= 3),
        ("one arm's two runs disagree on >= 2 tasks, each arm", "plain %d, cleat %d" % (flips["plain"], flips["cleat"]), min(flips.values()) >= 2),
        ("plain runs end with a mined verdict failing >= 12", "%d of %d" % (failing["plain"], runs_per_arm), failing["plain"] >= 12),
        ("cleat runs end with a mined verdict failing <= 3", "%d of %d" % (failing["cleat"], runs_per_arm), failing["cleat"] <= 3),
        ("cleat costs 20-60% more per task (median)", "%+.0f%%" % (100 * statistics.median(ratios)), 0.20 <= statistics.median(ratios) <= 0.60),
    ]
    print("tasks with every run: %d of %d" % (n, len(rows)))
    for name, value, held in checks:
        print("  %-54s %-40s %s" % (name, value, "holds" if held else "FALSIFIED"))
    print("  secondary — runs ending with cleat's real gates failing: plain %d, cleat %d of %d" % (gate["plain"], gate["cleat"], runs_per_arm))
    with open(os.path.join(work, "summary.json"), "w") as handle:
        json.dump(dict(rows=rows, checks=[dict(name=c[0], value=c[1], held=c[2]) for c in checks], real_gate=gate,
                       resolved=resolved, flips=flips), handle, indent=1)


if __name__ == "__main__":
    work = sys.argv[1]
    arms = {os.path.basename(p) for p in glob.glob(os.path.join(work, "runs", "*", "*"))}
    (repeated if any("-" in a for a in arms) else main)(work)

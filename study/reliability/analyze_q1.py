#!/usr/bin/env python3
"""analyze_q1 — verdict stability under each meaning-preserving transformation, and the
extract-method dodge. Reads data/meas/*.pkl, writes data/q1.json.

Two readings of "verdict", both reported:
  measurement  per function: over the ceiling or not (cc > 8 or length > 60); per escape site:
               flagged or not, sites matched by (file, enclosing function, kind); per clone
               pair: still found, pairs matched by (file, enclosing function) of both copies.
  gate         what the ratchet says when the baseline was written on the original and the
               transformed tree is judged (ratchet.judge, as check-complexity.py and
               check-escapes.py call it): a finding read as new or worsened fails the gate.
               For duplication: the density ratchet, and the changed-lines judgment (a clone
               overlapping a line the transformation changed fails).
"""
import collections, difflib, json, os, pickle, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
from common import ratchet

D = common.DATA


def load(name):
    with open(os.path.join(D, "meas", name + ".pkl"), "rb") as h:
        return pickle.load(h)


def keymap(variant):
    p = os.path.join(D, variant + ".keymap.json")
    if os.path.exists(p):
        with open(p) as h:
            return json.load(h)
    return {}


def back(km, c, rel, key):
    return km.get(c, {}).get(rel, {}).get(key, key)


def share(n, d):
    return {"n": n, "of": d, "share": round(n / d, 5) if d else None}


def complexity_l1(orig, var, km):
    tot = same_verdict = same_cc = same_len = unmatched = 0
    near = near_flip = 0
    near_black = []
    flips = []
    for c in orig:
        vmap = {(rel, back(km, c, rel, k)): v for (rel, k), v in var.get(c, {}).get("cx", {}).items()}
        for (rel, k), v in orig[c]["cx"].items():
            if k.startswith("@"):
                continue
            w = vmap.get((rel, k))
            if w is None:
                unmatched += 1
                continue
            tot += 1
            same_verdict += common.over(v) == common.over(w)
            same_cc += v[0] == w[0]
            same_len += v[1] == w[1]
            if common.over(v) != common.over(w) and len(flips) < 15:
                flips.append([c, rel, k, v[:2], w[:2]])
            if abs(v[1] - common.LINE_CEIL) <= 10:
                near += 1
                near_flip += (v[1] > common.LINE_CEIL) != (w[1] > common.LINE_CEIL)
    return {"functions_compared": tot, "unmatched": unmatched,
            "verdict_unchanged": share(same_verdict, tot), "cc_unchanged": share(same_cc, tot),
            "length_unchanged": share(same_len, tot),
            "within_10_of_60_lines": near, "60_line_verdict_flipped_near_ceiling": share(near_flip, near),
            "examples_flipped": flips}


def gate_judge(orig_entries, var_findings, metrics, renames):
    entries = [dict(e) for e in orig_entries]
    findings = [ratchet.Finding(e["file"], e["line"], e["text"], {k: v for k, v in e.items() if k not in ("file", "line", "text")})
                for e in var_findings]
    return ratchet.judge(findings, entries, metrics, renames=renames)


def complexity_l2(orig, var):
    out = collections.Counter()
    failing = []
    for c in orig:
        v = gate_judge(orig[c]["cx_findings"], var[c]["cx_findings"], ["cc", "lines"], True)
        out["baseline_entries"] += len(orig[c]["cx_findings"])
        for k in ("new", "worsened", "held", "improved", "changed", "stale"):
            out[k] += len(getattr(v, k))
        out["new_whose_function_was_renamed"] += sum("_q3r" in f.text for f in v.new)
        if v.failed:
            failing.append(c)
    d = dict(out)
    d["false_failures"] = share(out["new"] + out["worsened"], out["baseline_entries"])
    d["clones_gate_fails"] = share(len(failing), len(orig))
    d["clones_failing"] = failing
    return d


def esc_key(rec, km, c, site):
    rel, line, text, kind = site
    k = common.enclosing(rec["index"].get(rel, []), line)
    return (rel, back(km, c, rel, k), kind)


def escapes(orig, var, km):
    tot = kept = appeared = 0
    by_kind = collections.defaultdict(lambda: [0, 0])
    l2 = collections.Counter()
    failing = []
    for c in orig:
        a = collections.Counter(esc_key(orig[c], {}, c, s) for s in orig[c]["esc"])
        b = collections.Counter(esc_key(var[c], km, c, s) for s in var[c]["esc"])
        for k, n in a.items():
            tot += n
            kept += min(n, b.get(k, 0))
            by_kind[k[2]][0] += n
            by_kind[k[2]][1] += min(n, b.get(k, 0))
        appeared += sum(max(0, n - a.get(k, 0)) for k, n in b.items())
        oe = [f.entry() for f in common.escape_findings(orig[c]["esc"])]
        ve = [f.entry() for f in common.escape_findings(var[c]["esc"])]
        v = gate_judge(oe, ve, ["count"], False)
        l2["baseline_sites"] += len(oe)
        for k in ("new", "worsened", "held", "stale"):
            l2[k] += len(getattr(v, k))
        if v.failed:
            failing.append(c)
    gate = dict(l2)
    gate["false_failures"] = share(l2["new"] + l2["worsened"], l2["baseline_sites"])
    gate["clones_gate_fails"] = share(len(failing), len(orig))
    return {"sites": tot, "measurement_unchanged": share(kept, tot), "sites_appeared": appeared,
            "by_kind_kept": {k: share(v[1], v[0]) for k, v in by_kind.items()}, "gate": gate}


def pair_key(rec, km, c, locs):
    keys = []
    for rel, s, e in locs:
        k = common.enclosing(rec["index"].get(rel, []), s)
        keys.append((rel, back(km, c, rel, k)))
    return tuple(sorted(keys))


def changed_lines(c, variant, rels):
    out = {}
    for rel in rels:
        a = open(os.path.join(D, "orig", c, rel), encoding="utf-8").read().split("\n")
        p = os.path.join(D, variant, c, rel)
        b = open(p, encoding="utf-8").read().split("\n") if os.path.exists(p) else []
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        lines = set()
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                lines.update(range(j1 + 1, j2 + 1))
        if lines:
            out[rel] = lines
    return out


def duplication(orig, var, km, variant):
    tot = kept = new = 0
    dens_up = []
    touch_pairs = touch_tot = 0
    touching_clones = []
    per_clone = {}
    for c in orig:
        a = collections.Counter(pair_key(orig[c], {}, c, l) for l, _ in orig[c]["dup"])
        b = collections.Counter(pair_key(var[c], km, c, l) for l, _ in var[c]["dup"])
        tot += sum(a.values())
        kept += sum(min(n, b.get(k, 0)) for k, n in a.items())
        if a:
            per_clone[c] = round(sum(min(n, b.get(k, 0)) for k, n in a.items()) / sum(a.values()), 4)
        new += sum(max(0, n - a.get(k, 0)) for k, n in b.items())
        (d0, t0), (d1, t1) = orig[c]["density"], var[c]["density"]
        p0 = round(100.0 * d0 / t0, 2) if t0 else 0.0
        p1 = round(100.0 * d1 / t1, 2) if t1 else 0.0
        if p1 > p0:
            dens_up.append([c, p0, p1])
        rels = sorted({loc[0] for locs, _ in var[c]["dup"] for loc in locs})
        ch = changed_lines(c, variant, rels)
        t = 0
        for locs, _ in var[c]["dup"]:
            touch_tot += 1
            if any(rel in ch and any(s <= n <= e for n in ch[rel]) for rel, s, e in locs):
                touch_pairs += 1
                t += 1
        if t:
            touching_clones.append(c)
    vals = sorted(per_clone.values())
    big = ("psf__black", "pydantic__pydantic")
    rest_tot = sum(sum(1 for _ in orig[c]["dup"]) for c in orig if c not in big)
    return {"pairs": tot, "measurement_unchanged": share(kept, tot), "pairs_appeared": new,
            "per_clone_unchanged": per_clone, "per_clone_unchanged_median": vals[len(vals) // 2] if vals else None,
            "per_clone_changed_ge_10pct": share(sum(v <= 0.9 for v in vals), len(vals)),
            "density_gate_fails": share(len(dens_up), len(orig)), "density_rose": dens_up,
            "pairs_touching_changed_lines": share(touch_pairs, touch_tot),
            "changed_lines_gate_fails": share(len(touching_clones), len(orig))}


def lizard_versions(a, b):
    tot = same = same_cc = same_len = 0
    diffs = []
    for c in a:
        for k, v in a[c]["cx"].items():
            w = b[c]["cx"].get(k)
            if w is None:
                continue
            tot += 1
            same += common.over(v) == common.over(w)
            same_cc += v[0] == w[0]
            same_len += v[1] == w[1]
            if v[:2] != w[:2] and len(diffs) < 20:
                diffs.append([c, k[0], k[1], v[:2], w[:2]])
    only = sum(len(set(a[c]["cx"]) ^ set(b[c]["cx"])) for c in a)
    return {"versions": [a[next(iter(a))]["lizard"], b[next(iter(b))]["lizard"]], "functions_compared": tot,
            "functions_in_one_only": only, "verdict_unchanged": share(same, tot), "cc_identical": share(same_cc, tot),
            "length_identical": share(same_len, tot), "differences": diffs}


def extract_method(orig):
    plan = json.load(open(os.path.join(D, "extract-plan.json")))
    out = {}
    for variant, hkey in (("extract1", "helpers1"), ("extractN", "helpersN")):
        meas = load(variant)
        r = collections.Counter()
        for e in plan:
            c, rel, key = e["clone"], e["file"], e["key"]
            r["over_ceiling_functions"] += 1
            if not e[hkey]:
                r["not_extractable"] += 1
                continue
            r["extracted"] += 1
            cx = meas[c]["cx"]
            f = cx.get((rel, key))
            hs = [v for (rl, k), v in cx.items() if rl == rel and k.split("#")[0] in e[hkey]]
            if f is None or len(hs) != len(e[hkey]):
                r["unmeasured"] += 1
                continue
            r["measured"] += 1
            fn_clear = f[0] <= common.CC_CEIL and f[1] <= common.LINE_CEIL
            helpers_clear = all(h[0] <= common.CC_CEIL and h[1] <= common.LINE_CEIL for h in hs)
            total = f[0] + sum(h[0] for h in hs)
            r["function_verdict_clears"] += fn_clear
            r["function_and_helpers_all_under"] += fn_clear and helpers_clear
            r["total_cc_not_lower"] += total >= e["cc"]
            r["clears_and_total_cc_not_lower"] += fn_clear and total >= e["cc"]
            r["gate_passes_and_total_cc_not_lower"] += fn_clear and helpers_clear and total >= e["cc"]
            cc_over = e["cc"] > common.CC_CEIL
            r["cc_over_functions"] += cc_over
            r["cc_over_clears_cc"] += cc_over and f[0] <= common.CC_CEIL
        d = dict(r)
        n_all, n_m = r["over_ceiling_functions"], r["measured"]
        d["clears_share_of_all"] = share(r["function_verdict_clears"], n_all)
        d["clears_share_of_extracted"] = share(r["function_verdict_clears"], n_m)
        d["clears_with_total_cc_not_lower_share_of_all"] = share(r["clears_and_total_cc_not_lower"], n_all)
        d["clears_with_total_cc_not_lower_share_of_extracted"] = share(r["clears_and_total_cc_not_lower"], n_m)
        d["gate_passes_with_total_cc_not_lower_share_of_all"] = share(r["gate_passes_and_total_cc_not_lower"], n_all)
        d["why_not"] = collections.Counter(e.get("why_not1" if variant == "extract1" else "why_stopN") for e in plan).most_common()
        out[variant] = d
    return out


def main():
    orig = load("orig")
    res = {"sample": {"clones": len(orig), "files": sum(len(r["index"]) for r in orig.values()),
                      "functions": sum(len([k for k in r["cx"] if not k[1].startswith("@")]) for r in orig.values()),
                      "over_ceiling": sum(sum(common.over(v) for v in r["cx"].values()) for r in orig.values()),
                      "escape_sites": sum(len(r["esc"]) for r in orig.values()),
                      "clone_pairs": sum(len(r["dup"]) for r in orig.values()),
                      "per_clone": {c: {"functions": len(r["cx"]), "sites": len(r["esc"]), "pairs": len(r["dup"])} for c, r in orig.items()}}}
    for variant in ("rename", "reorder", "unparse", "black"):
        var = load(variant)
        km = keymap(variant)
        res[variant] = {"complexity": complexity_l1(orig, var, km), "complexity_gate": complexity_l2(orig, var),
                        "escapes": escapes(orig, var, km), "duplication": duplication(orig, var, km, variant)}
        # reorder/rename/reformat-by-black on the escape lines: why the gate reads a site as new

        print(variant, "done", file=sys.stderr)
    res["lizard_1.24.0_vs_1.24.1"] = lizard_versions(load("orig_lizard1240"), orig)
    res["extract_method"] = extract_method(orig)
    with open(os.path.join(D, "q1.json"), "w") as h:
        json.dump(res, h, indent=1, default=list)


if __name__ == "__main__":
    main()

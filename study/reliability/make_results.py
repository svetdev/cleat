#!/usr/bin/env python3
"""make_results — gather data/*.json into q3/results.json, with the seed tallies per gate,
per language, and against the pre-registered thresholds."""
import collections, json, os
HERE = os.path.dirname(os.path.abspath(__file__))
CLEAT = os.path.dirname(os.path.dirname(HERE))
Q3 = os.path.join(os.environ.get("STUDY_WORK", ".study-work"), "q3")
D = os.path.join(Q3, "data")
L = lambda n: json.load(open(os.path.join(D, n)))


def share(n, d):
    return {"n": n, "of": d, "share": round(n / d, 4) if d else None}


def seeds():
    r = L("seeds-result.json")
    per = {}
    tot = collections.defaultdict(lambda: [0, 0])
    guesses = collections.Counter()
    for lang, v in r.items():
        agg = collections.defaultdict(lambda: [0, 0, 0])
        for s in v["seeds"]:
            if s.get("caught") is None:
                continue
            k = s["category"] + ({"t1": "_verbatim", "t2": "_type2"}.get(s.get("kind"), ""))
            agg[k][0] += bool(s["caught"])
            agg[k][1] += 1
            agg[k][2] += bool(s.get("error"))
            if lang != "swift-swiftlint":
                tot[k][0] += bool(s["caught"])
                tot[k][1] += 1
            if s.get("expect") in ("caught", "missed"):
                guesses["right" if (s["expect"] == "caught") == bool(s["caught"]) else "wrong"] += 1
        per[lang] = {"complexity_tool": v["complexity_tool"], "escape_languages": v["escape_languages"],
                     "clean_project_green": {k: (x["rc"] if isinstance(x, dict) else x) for k, x in v["clean"].items() if k != "layering_false_violations_on_clean_code"},
                     "layering_false_violations_on_clean_code": v["clean"].get("layering_false_violations_on_clean_code", []),
                     "sensitivity": {k: share(a, b) for k, (a, b, e) in agg.items()},
                     "errors": {k: e for k, (a, b, e) in agg.items() if e},
                     "missed": [{"id": s["id"], "category": s["category"], "desc": s["desc"]} for s in v["seeds"] if s.get("caught") is False]}
    return {"per_language": per, "all_languages_lizard_path": {k: share(a, b) for k, (a, b) in tot.items()},
            "author_guesses_recorded_before_the_run": dict(guesses)}


def main():
    q1 = L("q1.json")
    for v in ("rename", "reorder", "unparse", "black"):
        q1[v]["duplication"].pop("density_rose", None)
    man = L("manifest.json")
    res = {
        "study": "Q3 reliability — pre-registered in DECISIONS.md, 2026-10-24, 'Prediction: the verdicts hold under renames and reorders, not under reformat, and extraction dodges complexity'",
        "cleat_commit": os.popen("git -C %s rev-parse HEAD" % CLEAT + "").read().strip(),
        "config": "attach defaults (attach.py --into a copy): cyclomatic 8, 60 lines, duplication min_lines 6; data/attach-quality.json",
        "sample": {"repositories": [{"clone": m["clone"], "commit": m["commit"], "eligible_production_files": m["eligible"], "sampled_files": m["sampled"]} for m in man],
                   **{k: v for k, v in q1["sample"].items() if k != "per_clone"}, "per_clone": q1["sample"]["per_clone"]},
        "transform_stats": L("transform-stats.json"),
        "q1_transformations": {k: q1[k] for k in ("rename", "reorder", "unparse", "black")},
        "q1_lizard_patch_release": {"python": q1["lizard_1.24.0_vs_1.24.1"], **L("lizard-versions-other.json")},
        "q1_extract_method": q1["extract_method"],
        "q1_real_gate_check": L("verify-gate.json"),
        "q1_semantic_check_test_suites": L("semantics.json"),
        "q2_seeded_defects": seeds(),
        "q2_corpus_frozen": json.load(open(os.path.join(HERE, "corpus/FROZEN.json"))) if os.path.exists(os.path.join(Q3, "corpus/FROZEN.json")) else None,
        "q3_instrument_agreement": L("q3.json"),
    }
    with open(os.path.join(Q3, "results.json"), "w") as h:
        json.dump(res, h, indent=1)
    s = res["q2_seeded_defects"]
    print(json.dumps(s["all_languages_lizard_path"], indent=0), s["author_guesses_recorded_before_the_run"])
    for lang, v in s["per_language"].items():
        print(lang, {k: "%d/%d" % (x["n"], x["of"]) for k, x in v["sensitivity"].items()}, v["errors"], [m["id"] for m in v["missed"] if m["category"] == "complexity"])


if __name__ == "__main__":
    main()

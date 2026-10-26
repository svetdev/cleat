"""Stage 4: the most informative dodge and relocation examples, anonymized, from events.jsonl.
Usage: examples.py OUTDIR  -> OUTDIR/examples.md

Informative = a dodge on cyclomatic (not length alone) whose before and after are both in
the record, ranked hook-handed first, then by the cc the gate reported, one per project and
language where possible; plus the clearest relocation."""
import json, os, re, sys

FENCE = {"rust": "rust", "typescript": "ts", "swift": "swift", "python": "python"}
# names to scrub come from the private WORK/anon.json (see classify.py), never from this file
_W = os.environ.get("WORK", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "q4_work"))
_N = json.load(open(os.path.join(_W, "anon.json")))["names"] if os.path.exists(os.path.join(_W, "anon.json")) else []
SCRUB = re.compile("|".join(re.escape(n) for n in _N) or r"(?!x)x", re.I)


def scrub(s):
    return SCRUB.sub("acme", s or "")


def main():
    outdir = sys.argv[1]
    rows = [json.loads(l) for l in open(os.path.join(outdir, "events.jsonl"))]
    cands = []
    for r in rows:
        for f in r["evidence"].get("functions", []):
            if f.get("verdict") == "dodge" and f.get("before_code") and f.get("after_code") and f["cc_before"] > 8:
                cands.append((r, f))
    cands.sort(key=lambda rf: (not rf[0]["handed_back_by_hook"], -rf[1]["cc_before"], rf[0]["eid"]))
    picked, seen = [], set()
    for r, f in cands:
        key = (r["project"], r["language"], r["session"])
        if key in seen:
            continue
        seen.add(key)
        picked.append((r, f))
        if len(picked) == 4:
            break
    for r, f in cands:          # fill up if diversity left gaps
        if len(picked) >= 4:
            break
        if (r, f) not in picked:
            picked.append((r, f))
    reloc = [r for r in rows if r["category"] == "escape relocated"]
    out = ["# Dodge and relocation examples", "",
           "Anonymized: project ids as in events.jsonl, product names replaced by `acme`. cc numbers are lizard's "
           "(the gate's own for *before*; lizard on the code the agent wrote for *after* and the helpers). "
           "A dodge, by the pre-registered rule: every new helper has exactly one caller and "
           "cc(function after) + sum cc(helpers) >= cc(function before).", ""]
    for n, (r, f) in enumerate(picked, 1):
        lang = FENCE.get(r["language"], "")
        out += ["## %d. %s, %s `%s` (%s, %s, %s)" % (n, r["project"], r["gate"], f["function"], r["language"],
                                                 "hook-handed" if r["handed_back_by_hook"] else "self-run", r["mode"]),
                "",
                "event %d, file `%s`; cc before %d; after: function %d + helpers %s = %d; callers per helper %s. %s" % (
                    r["eid"], scrub(r["file"]), f["cc_before"], f["cc_after"],
                    json.dumps(f["helpers"]), f["sum_after"], json.dumps(f["callers"]),
                    "Reconstructed: " + f["reconstructed"] if f.get("reconstructed") else "The edit wrote the function whole."),
                "", "Before:", "", "```" + lang, scrub(f["before_code"]).rstrip()[:3500], "```", "",
                "After (function, then its new helpers):", "", "```" + lang, scrub(f["after_code"]).rstrip()[:3500], "```", ""]
    if reloc:
        r = sorted(reloc, key=lambda x: (not x["handed_back_by_hook"], x["eid"]))[0]
        out += ["## %d. %s, escapes relocated (%s, %s)" % (len(picked) + 1, r["project"], r["language"],
                                                        "hook-handed" if r["handed_back_by_hook"] else "self-run"), "",
                "event %d: flagged in `%s`: %s" % (r["eid"], scrub(r["file"]), "; ".join(scrub(i) for i in r["items"][:3])),
                "", "Added in the same window (the flagged site passed at the next run):", ""]
        for h in r["evidence"]["relocated"][:4]:
            out.append("- `%s` in `%s` (kind %s; test file: %s)" % (scrub(h["line"]), scrub(h["file"]), h["kind"],
                                                                  h["test_file"]))
        out.append("")
        others = [x for x in reloc if x is not r]
        for x in others:
            h = x["evidence"]["relocated"][0]
            out.append("Also: event %d (%s, %s): flagged %s, added `%s` in `%s`." % (
                x["eid"], x["project"], x["language"], ", ".join(h["flagged"]), scrub(h["line"])[:100], scrub(h["file"])))
        out.append("")
    open(os.path.join(outdir, "examples.md"), "w").write("\n".join(out))
    print("\n".join(out)[:3000])


if __name__ == "__main__":
    main()

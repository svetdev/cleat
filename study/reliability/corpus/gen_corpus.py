#!/usr/bin/env python3
"""gen_corpus — write every seed to corpus/seeds/<lang>.json and freeze the corpus (sha256 of
each source and seed file, with the time) in corpus/FROZEN.json, before any gate runs on it."""
import datetime, hashlib, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import templates, escapes, structural

LANGS = ["python", "typescript", "swift", "rust", "go", "kotlin", "java", "ruby", "shell"]


def main():
    os.makedirs(os.path.join(HERE, "seeds"), exist_ok=True)
    for lang in LANGS:
        seeds = {"complexity": structural.complexity_seeds(lang) if lang in structural.SYNTAX else [],
                 "duplication": structural.duplication_seeds(lang),
                 "escapes_literal": [], "escapes_intent": [],
                 "layering": structural.layering_seeds(lang), "dead_symbols": structural.dead_seeds(lang)}
        for i, row in enumerate(escapes.LITERAL[lang]):
            name, text = row[0], row[1]
            f = row[2] if len(row) > 2 else templates.STATS_FILE[lang]
            seeds["escapes_literal"].append({"id": "%s-lit-%d" % (lang, i), "desc": name, "mode": "append" if f == templates.STATS_FILE[lang] or os.path.basename(f).startswith("format") else "new",
                                             "file": f, "text": text, "expect": "caught"})
        for i, row in enumerate(escapes.INTENT[lang]):
            name, text, expect = row[0], row[1], row[2]
            f = row[3] if len(row) > 3 else templates.STATS_FILE[lang]
            seeds["escapes_intent"].append({"id": "%s-int-%d" % (lang, i), "desc": name, "mode": "append" if f == templates.STATS_FILE[lang] else "new",
                                            "file": f, "text": text, "expect": expect})
        with open(os.path.join(HERE, "seeds", lang + ".json"), "w") as h:
            json.dump(seeds, h, indent=1)
    frozen = {"frozen_at": datetime.datetime.now().isoformat(timespec="seconds"), "sha256": {}}
    for d in (HERE, os.path.join(HERE, "seeds")):
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if os.path.isfile(p) and f.endswith((".py", ".json")) and f != "FROZEN.json":
                frozen["sha256"][os.path.relpath(p, HERE)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    with open(os.path.join(HERE, "FROZEN.json"), "w") as h:
        json.dump(frozen, h, indent=1)
    print(json.dumps({l: {k: len(v) for k, v in json.load(open(os.path.join(HERE, "seeds", l + ".json"))).items()} for l in LANGS}))


if __name__ == "__main__":
    main()

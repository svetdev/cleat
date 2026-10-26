#!/usr/bin/env python3
"""seed_run — build each language's clean project, attach cleat (attach.py --into), add the
layering and dead-symbol sections, commit, check every gate is green, then inject each frozen
seed as an uncommitted change, run that category's gate (the project's own copy of
quality/bin/check-*.py), record the exit code, and reset.

Caught = exit 1. Exit 2 (the gate could not run) is recorded as an error, never as caught.
Writes data/seeds-result.json.
"""
import json, os, re, shutil, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
Q3 = os.path.join(os.environ.get("STUDY_WORK", ".study-work"), "q3")
sys.path.insert(0, os.path.join(HERE, "corpus"))
import templates, structural

CLEAT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(Q3, "tmp", "proj")
NODE_BIN = os.path.join(Q3, "node", "node_modules", ".bin")
ENV = dict(os.environ, PATH=NODE_BIN + os.pathsep + os.environ["PATH"], PYTHONDONTWRITEBYTECODE="1", CLEAT_HOOKS="off")
GATE = {"complexity": "check-complexity.py", "duplication": "check-duplication.py", "escapes_literal": "check-escapes.py",
        "escapes_intent": "check-escapes.py", "layering": "check-layering.py", "dead_symbols": "check-dead-symbols.py"}
LAYER_LANG = {"python": ("imports", "python"), "typescript": ("imports", "typescript"), "rust": ("imports", "rust"),
              "go": ("imports", "go"), "kotlin": ("imports", "kotlin"), "java": ("imports", "java"),
              "swift": ("identifiers", "swift"), "ruby": ("ast-grep", "ruby")}


def sh(cmd, cwd, check=True):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=ENV)
    if check and p.returncode != 0:
        raise RuntimeError("%s failed: %s %s" % (cmd, p.stdout[-500:], p.stderr[-500:]))
    return p


def git(cwd, *a):
    return sh(["git", "-c", "user.email=q3@example.com", "-c", "user.name=q3", *a], cwd)


def build(lang, variant=None):
    name = lang if variant is None else "%s-%s" % (lang, variant)
    d = os.path.join(WORK, name)
    if os.path.exists(d):
        shutil.rmtree(d)
    os.makedirs(d)
    for rel, text in templates.T[lang].items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as h:
            h.write(text)
    git(d, "init", "-q", "-b", "main")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "clean")
    attach = sh([sys.executable, os.path.join(CLEAT, "quality/bin/attach.py"), "--into", "."], d)
    with open(os.path.join(d, "quality.json")) as h:
        cfg = json.load(h)
    if lang in LAYER_LANG:
        reader, language = LAYER_LANG[lang]
        cfg["layering"] = {"references": reader, "language": language, "extensions": [templates.EXT[lang]],
                           "allowed": {"core": [], "base": [], "models": ["core", "base"], "services": ["models", "core", "base"], "App": None},
                           "always_allowed": [], "skip_dirs": [], "roots": [{"root": "src", "exempt": []}]}
        cfg["dead_symbols"] = {"roots": ["src"], "language": lang, "exclude": [], "ignore": [], "exempt": {}, "enforcement": "block"}
    if variant == "swiftlint":
        cfg["complexity"] = {"tool": "swiftlint", "sources": ["src"], "ceilings": {"cc": 8, "lines": 60},
                             "baseline": "quality/complexity-baseline.json"}
    with open(os.path.join(d, "quality.json"), "w") as h:
        json.dump(cfg, h, indent=2)
    if variant == "swiftlint":
        sh([sys.executable, "quality/bin/check-complexity.py", "--write-baseline"], d)
    # a clean project must be green; dead symbols the template genuinely has go to exempt with a reason
    if "dead_symbols" in cfg:
        p = sh([sys.executable, "quality/bin/check-dead-symbols.py"], d, check=False)
        if p.returncode == 1:
            for line in p.stdout.split("\n"):
                line = line.strip()
                if ":" in line and "  " in line and not line.startswith(("FAIL", "REPORT", "Delete")):
                    loc, sym = line.split("  ", 1)
                    cfg["dead_symbols"]["exempt"]["%s:%s" % (loc.rsplit(":", 1)[0], sym.strip())] = "the clean template's entry point"
            with open(os.path.join(d, "quality.json"), "w") as h:
                json.dump(cfg, h, indent=2)
    clean_false = []
    if "layering" in cfg:
        # a clean template whose allowed, downward imports the gate still refuses: record them,
        # exempt them (keyed by file and name, as a person would), so the seeds can be judged at all
        p = sh([sys.executable, "quality/bin/check-layering.py"], d, check=False)
        if p.returncode == 1:
            for line in p.stdout.split("\n"):
                m = re.match(r"^\s+(\S+):(\d+) -> (\S+) \((\S+)\)$", line)
                if m:
                    clean_false.append(line.strip())
                    cfg["layering"]["roots"][0]["exempt"].append({"file": m.group(1), "name": m.group(3),
                                                                 "reason": "an allowed downward import the gate refuses on the clean template"})
            with open(os.path.join(d, "quality.json"), "w") as h:
                json.dump(cfg, h, indent=2)
    git(d, "add", "-A")
    git(d, "commit", "-qm", "attach")
    clean = {"layering_false_violations_on_clean_code": clean_false}
    for cat, gate in sorted(set(GATE.items())):
        section = {"complexity": "complexity", "duplication": "duplication", "layering": "layering", "dead_symbols": "dead_symbols"}.get(cat, "escapes")
        if section not in cfg:
            clean[cat] = None
            continue
        p = sh([sys.executable, "quality/bin/" + gate], d, check=False)
        clean[cat] = {"rc": p.returncode, "out": (p.stdout + p.stderr)[-400:]}
    return d, cfg, clean, attach.stdout[-1500:]


def apply(d, seed):
    p = os.path.join(d, seed["file"])
    mode = seed["mode"]
    if mode == "new" or not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as h:
            h.write(seed["text"] + ("\n" if not seed["text"].endswith("\n") else ""))
        return
    text = open(p).read()
    if mode == "append":
        text = text.rstrip("\n") + "\n\n\n" + seed["text"] + "\n"
    elif mode == "insert_after":
        assert seed["anchor"] in text, (seed["id"], seed["anchor"])
        text = text.replace(seed["anchor"], seed["anchor"] + seed["text"], 1)
    elif mode == "insert_top":
        lines = text.split("\n")
        at = next((i + 1 for i, l in enumerate(lines) if l.startswith("package ")), 0)
        lines[at:at] = seed["text"].split("\n")
        text = "\n".join(lines)
    elif mode == "insert_before_last_brace":
        k = text.rstrip().rfind("}")
        text = text[:k] + "\n" + seed["text"] + "\n" + text[k:]
    with open(p, "w") as h:
        h.write(text)


def reset(d):
    git(d, "checkout", "-q", "--", ".")
    git(d, "clean", "-fdq")


def run(lang, variant=None, only=None):
    d, cfg, clean, attach_out = build(lang, variant)
    seeds = json.load(open(os.path.join(HERE, "corpus", "seeds", lang + ".json")))
    res = {"project": d, "clean": clean, "config_sections": sorted(cfg), "escape_languages": cfg.get("escapes", {}).get("languages"),
           "complexity_tool": cfg.get("complexity", {}).get("tool"), "seeds": []}
    for cat, items in seeds.items():
        if only and cat not in only:
            continue
        section = {"complexity": "complexity", "duplication": "duplication", "layering": "layering", "dead_symbols": "dead_symbols"}.get(cat, "escapes")
        if section not in cfg:
            for s in items:
                res["seeds"].append(dict(s, category=cat, rc=None, caught=None, note="gate not configured by attach"))
            continue
        for s in items:
            apply(d, s)
            p = sh([sys.executable, "quality/bin/" + GATE[cat]], d, check=False)
            out = p.stdout + p.stderr
            res["seeds"].append({"id": s["id"], "category": cat, "desc": s["desc"], "expect": s.get("expect"), "kind": s.get("kind"),
                                 "rc": p.returncode, "caught": p.returncode == 1, "error": p.returncode not in (0, 1),
                                 "out": out[-600:]})
            reset(d)
    return res


def main():
    langs = sys.argv[1:] or ["python", "typescript", "swift", "rust", "go", "kotlin", "java", "ruby", "shell"]
    os.makedirs(WORK, exist_ok=True)
    out = {}
    for lang in langs:
        out[lang] = run(lang)
        print(lang, sum(1 for s in out[lang]["seeds"] if s["caught"]), len(out[lang]["seeds"]), out[lang]["clean"], file=sys.stderr)
    if "swift" in langs:
        out["swift-swiftlint"] = run("swift", "swiftlint", only={"complexity"})
    with open(os.path.join(Q3, "data", "seeds-result.json"), "w") as h:
        json.dump(out, h, indent=1)


if __name__ == "__main__":
    main()

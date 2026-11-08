#!/usr/bin/env python3
"""share — a replay's results as a card anyone can contribute without naming their code.

  share.py card <work dir> [up to change] [--note TEXT]    write study/contributed/<random id>.json
  share.py check <card …>                                  what CI runs on every contributed card

A card holds only the fields in SCHEMA: the language and the runner by kind, the size at the
start in a bucket, the counts of changes and checkpoints, and per checkpoint and arm the
complexity, resolution, probe cost and mutant counts. It holds no repository name, path, file
name, commit text, date, command or code. The id is random. A run that stopped early is shared
as it stands (`status` says so): a run that found nothing counts as much as one that did.
`check` refuses a card with a field outside the schema, a size that is not a bucket, a note
over NOTE_MAX characters, or an oracle that resolved less than 90% of its scored changes.
Cards are pooled with each other only, never with the study's own runs (DECISIONS.md,
2026-11-07).
"""

import json
import os
import re
import secrets
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRIBUTED = os.path.join(os.path.dirname(HERE), "contributed")
NOTE_MAX = 280
ORACLE_SHARE = 0.9
BUCKETS = ((2000, "under 2k"), (5000, "2k to 5k"), (10000, "5k to 10k"), (20000, "10k to 20k"),
           (50000, "20k to 50k"), (100000, "50k to 100k"), (None, "over 100k"))
RUNNERS = ("pytest", "unittest", "jest", "vitest", "mocha", "nextest", "cargo", "gotestsum", "go", "gradle",
           "maven", "mvn", "rspec", "minitest", "phpunit", "dotnet", "swift", "xcodebuild", "mix", "ctest")
LANGUAGES = ("python", "typescript", "javascript", "rust", "go", "java", "kotlin", "swift", "ruby", "php",
             "csharp", "c", "cpp", "elixir", "scala", "dart", "shell", "other")
ARMS = ("plain", "cleat")
MUTANT_KINDS = ("written", "control", "types")
TEST_SETS = ("own", "upstream")

NUM = (int, float)
SCHEMA = {
    "schema": int, "id": str, "cleat": str, "language": str, "runner": str, "size": str,
    "changes": int, "scored": int, "checkpoints": [int], "status": str, "note": str,
    "oracle": {"scored": int, "resolved": int},
    "arms": {arm: {"trees": int, "done": [int], "scored": int, "resolved": int, "cost": NUM,
                   "density": {"*": NUM}} for arm in ARMS} | {"upstream": {"density": {"*": NUM}}},
    "probes": {"*": {"runs": int, "resolved": int, "cost": NUM}},
    "mutants": {"*": {arm: [[int]] for arm in ARMS} | {"upstream": [[int]]}},
}


def bucket(lines):
    return next(name for top, name in BUCKETS if top is None or lines < top)


def runner(test_command):
    """The runner's kind: the first known runner the command names, never the command."""
    words = re.findall(r"[a-z][a-z0-9_-]*", (test_command or "").lower())
    return next((w for w in words if w in RUNNERS), "other")


def shape_errors(value, shape, where="card"):
    """Every way `value` departs from `shape`; "*" stands for any key."""
    if shape is NUM:
        return [] if isinstance(value, NUM) and not isinstance(value, bool) else ["%s is not a number" % where]
    if isinstance(shape, type):
        return [] if isinstance(value, shape) and not isinstance(value, bool) else ["%s is not %s" % (where, shape.__name__)]
    if isinstance(shape, list):
        if not isinstance(value, list):
            return ["%s is not a list" % where]
        return [e for i, v in enumerate(value) for e in shape_errors(v, shape[0], "%s[%d]" % (where, i))]
    if not isinstance(value, dict):
        return ["%s is not an object" % where]
    out = []
    for key, v in value.items():
        sub = shape.get(key, shape.get("*"))
        out += ["%s.%s is not a field a card may hold" % (where, key)] if sub is None else shape_errors(v, sub, "%s.%s" % (where, key))
    return out


def check(card):
    """Every reason this card cannot be contributed."""
    out = shape_errors(card, SCHEMA)
    if out:
        return out
    missing = [k for k in SCHEMA if k not in card]
    out += ["card has no %s" % k for k in missing]
    out += [] if card.get("schema") == 1 else ["schema is not 1"]
    out += [] if re.fullmatch(r"[0-9a-f]{12}", card.get("id", "")) else ["id is not 12 hex digits"]
    out += [] if re.fullmatch(r"[0-9a-f]{40}", card.get("cleat", "")) else ["cleat is not a commit"]
    out += [] if card.get("size") in [name for _, name in BUCKETS] else ["size is not a bucket"]
    out += [] if card.get("language") in LANGUAGES else ["language is not one of: " + ", ".join(LANGUAGES)]
    out += [] if card.get("runner") in RUNNERS + ("other",) else ["runner is not a known kind"]
    out += [] if card.get("status") in ("complete", "stopped") else ["status is not complete or stopped"]
    out += [] if len(card.get("note", "")) <= NOTE_MAX else ["note is over %d characters" % NOTE_MAX]
    oracle = card.get("oracle", {})
    if oracle.get("resolved", 0) < ORACLE_SHARE * oracle.get("scored", 0) or not oracle.get("scored"):
        out.append("the oracle resolved %s of %s scored changes, under 90%%: the harness or adapter was failing"
                   % (oracle.get("resolved"), oracle.get("scored")))
    return out


def language_and_runner(plan_lang):
    """The card's language and runner kind from the plan's language: a built-in name or an adapter."""
    if plan_lang in ("ts", "rust"):
        return ("typescript", "vitest") if plan_lang == "ts" else ("rust", "cargo")
    with open(plan_lang) as handle:
        spec = json.load(handle)
    names = [n for n in spec.get("languages") or [] if n in LANGUAGES]
    return (names[0] if names else "other"), runner(spec.get("test"))


def mutant_counts(work, points):
    """{"checkpoint/kind/tests": {arm: [[killed, survived] per tree]}} from mutate.py's results."""
    out = {}
    for point in points:
        base = os.path.join(work, "mutants", str(point))
        for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            m = re.fullmatch(r"(plain|cleat|upstream)-?\d*-(own|upstream)(?:-(control|types))?\.jsonl", name)
            if not m:
                continue
            with open(os.path.join(base, name)) as handle:
                status = [json.loads(line)["status"] for line in handle]
            key = "%d/%s/%s" % (point, m.group(3) or "written", m.group(2))
            out.setdefault(key, {}).setdefault(m.group(1), []).append([status.count("killed"), status.count("survived")])
    return out


def upstream_measures(work):
    """{checkpoint: measure} of upstream's tree, as analyze.py cached it; empty before analyze ran."""
    path = os.path.join(work, "upstream.jsonl")
    if not os.path.isfile(path):
        return {}
    with open(path) as handle:
        return {row["index"]: row["measure"] for row in map(json.loads, handle)}


def setups(work, names):
    out = []
    for name in names:
        path = os.path.join(work, "chains", name, "setup.json")
        if os.path.isfile(path):
            with open(path) as handle:
                out.append(json.load(handle))
    return out


def arm_summary(analyze, trees, arm, points):
    mine = [t for t in trees.values() if t["arm"] == arm]
    costs = [c for t in mine for c in t["costs"] if c is not None]
    return dict(trees=len(mine), done=[t["done"] for t in mine], scored=sum(t["scored"] for t in mine),
                resolved=sum(t["resolved"] for t in mine), cost=round(statistics.median(costs), 2) if costs else 0,
                density={str(p): round(d, 2) for p in points
                         for d in [analyze.density([t["measure"][p] for t in mine if p in t["measure"]])] if d is not None})


def card(work, upto=None, note=""):
    """The card for one project's work dir, read through change `upto`."""
    sys.path.insert(0, HERE)
    import analyze
    with open(os.path.join(work, "plan.json")) as handle:
        plan = json.load(handle)
    upto = int(upto or len(plan["changes"]))
    points = tuple(sorted(int(k) for k in plan["probes"] if int(k) <= upto))
    analyze.POINTS = points
    trees = {name: dict(analyze.tree_report(steps), arm=name.split("-")[0]) for name, steps in analyze.chains(work, upto).items()}
    oracle = max((t for t in trees.values() if t["arm"] == "oracle"), key=lambda t: t["resolved"], default=None)
    found = setups(work, sorted(trees))
    language, kind = language_and_runner(plan["lang"])
    upstream = upstream_measures(work)
    probes = analyze.probe_report(analyze.probes(work))
    return dict(
        schema=1, id=secrets.token_hex(6), cleat=next((s["cleat"] for s in found if s.get("cleat")), ""),
        language=language, runner=kind, size=bucket(found[0]["measure"]["lines"]) if found else "",
        changes=upto, scored=sum(1 for c in plan["changes"][:upto] if c.get("scored")), checkpoints=list(points),
        status="complete" if all(t["done"] >= upto for t in trees.values()) else "stopped", note=note[:NOTE_MAX],
        oracle=dict(scored=oracle["scored"], resolved=oracle["resolved"]) if oracle else dict(scored=0, resolved=0),
        arms={arm: arm_summary(analyze, trees, arm, points) for arm in ARMS}
        | {"upstream": {"density": {str(p): round(m["over8_per_kloc"], 2) for p, m in upstream.items() if p in points}}},
        probes={k: dict(runs=v["runs"], resolved=v["resolved"], cost=round(v["cost"] or 0, 2))
                for k, v in probes.items() if v["runs"]},
        mutants=mutant_counts(work, points))


def write_card(work, upto=None, note=""):
    got = card(os.path.abspath(work), upto, note)
    problems = check(got)
    print(json.dumps(got, indent=1))
    if problems:
        raise SystemExit("not shareable:\n  " + "\n  ".join(problems))
    os.makedirs(CONTRIBUTED, exist_ok=True)
    path = os.path.join(CONTRIBUTED, got["id"] + ".json")
    with open(path, "w") as handle:
        json.dump(got, handle, indent=1)
        handle.write("\n")
    print("\nwrote %s: this is everything it shares. Read it, then open a pull request with that one file." % path)


def check_files(paths):
    failed = 0
    for path in paths:
        try:
            with open(path) as handle:
                problems = check(json.load(handle))
        except (OSError, ValueError) as err:
            problems = [str(err)]
        name = os.path.basename(path)
        if problems or not re.fullmatch(r"[0-9a-f]{12}\.json", name):
            failed += 1
            print("%s:\n  %s" % (path, "\n  ".join(problems or ["the file is not named <id>.json"])))
    print("%d card(s), %d refused" % (len(paths), failed))
    return 1 if failed else 0


def main(argv):
    if argv[:1] == ["check"]:
        return check_files(argv[1:])
    if argv[:1] == ["card"] and len(argv) >= 2:
        note = argv[argv.index("--note") + 1] if "--note" in argv else ""
        rest = [a for i, a in enumerate(argv[1:], 1) if a != "--note" and argv[i - 1] != "--note"]
        write_card(rest[0], rest[1] if len(rest) > 1 else None, note)
        return 0
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

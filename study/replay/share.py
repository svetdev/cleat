#!/usr/bin/env python3
"""share — a replay's results as a card anyone can contribute without naming their code.

  share.py card <work dir> [up to change] [--note TEXT]    write study/contributed/<random id>.json
  share.py state <attached tree> [history.json] [--note TEXT] [--feels easy|mixed|breaks] [--ai most|some|none]
                                                           the same, with no replay and no agent
  share.py open <card file>                                opens the card's issue link in the browser
  share.py text <card file>                                the card in words, to paste as a comment
  share.py take [file]                                     a card pasted in an issue or a comment, checked
                                                           and written to study/contributed/ (stdin without a file)
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

A state card (`"kind": "state"`) is where a codebase stands, for someone who runs no replay: per
part of the tree its language, its size in a bucket and what `gate.py --summary` measured per
1,000 lines, the duplicated share, and, when `replay/history.py`'s file for the repository is
given, how many of its merged commits the gates would have refused and by which gate. A part
goes in without its name. The whole tree's size, its age from first commit to last and its
number of commits go in as buckets. `--feels` and `--ai` are the owner's own answers to two
questions: whether adding a feature is easy, mixed or tends to break things, and whether most,
some or none of the code was written by an AI agent. `gated` says whether the repository
already carried cleat before the tree was attached.

`text` writes a state card as a few lines a person can read, each measure beside its standing
in the reference sample (pNN: the share of that language's sampled parts with less). `take`
reads that form back as well as the JSON, so a card pasted as a comment is a card.
"""

import json
import os
import re
import secrets
import statistics
import subprocess
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRIBUTED = os.path.join(os.path.dirname(HERE), "contributed")
NOTE_MAX = 280
ISSUES = "https://github.com/svetdev/cleat/issues/new"
LINK_MAX = 6000
ISSUE_TOP = "A card from `study/try-it.md`: counts and ranges, with no repository name, path or code."
ISSUE_END = "Anything that failed or looked wrong (optional):"
FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
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
STATE_SCHEMA = {
    "schema": int, "kind": str, "id": str, "cleat": str, "note": str, "duplicated": NUM,
    "size": str, "age": str, "commits": str, "feels": str, "ai": str, "gated": bool,
    "parts": [{"language": str, "size": str, "measures": {"*": NUM}}],
    "history": {"commits": int, "refused": int, "gates": {"*": int}},
}
MEASURES = ("complexity_per_kloc", "over_cc_per_kloc", "over_lines_per_kloc", "escapes_per_kloc", "test_ratio",
            "assertions_per_test", "unchecked_share")
GATE_NAME = r"[a-z][a-z-]{1,30}"
AGES = ((90, "under 3 months"), (365, "3 to 12 months"), (730, "1 to 2 years"), (1825, "2 to 5 years"),
        (None, "over 5 years"))
COMMITS = ((100, "under 100"), (500, "100 to 500"), (2000, "500 to 2k"), (10000, "2k to 10k"), (None, "over 10k"))
FEELS = ("easy", "mixed", "breaks", "unsaid")
AI = ("most", "some", "none", "unsaid")


def bucket(count, table=BUCKETS):
    return next(name for top, name in table if top is None or count < top)


def names(table):
    return [name for _, name in table]


def runner(test_command):
    """The runner's kind: the first known runner the command names, never the command."""
    words = re.findall(r"[a-z][a-z0-9_-]*", (test_command or "").lower())
    return next((w for w in words if w in RUNNERS), "other")


def shape_errors(value, shape, where="card"):
    """Every way `value` departs from `shape`; "*" stands for any key."""
    if shape is NUM:
        return [] if isinstance(value, NUM) and not isinstance(value, bool) else ["%s is not a number" % where]
    if isinstance(shape, type):
        fits = isinstance(value, shape) and (shape is bool or not isinstance(value, bool))
        return [] if fits else ["%s is not %s" % (where, shape.__name__)]
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


def common_errors(card, schema):
    """What every kind of card must hold: its fields, a random id, cleat's commit, a short note."""
    out = ["card has no %s" % k for k in schema if k not in card]
    out += [] if card.get("schema") == 1 else ["schema is not 1"]
    out += [] if re.fullmatch(r"[0-9a-f]{12}", card.get("id", "")) else ["id is not 12 hex digits"]
    out += [] if re.fullmatch(r"[0-9a-f]{40}", card.get("cleat", "")) else ["cleat is not a commit"]
    out += [] if len(card.get("note", "")) <= NOTE_MAX else ["note is over %d characters" % NOTE_MAX]
    return out


def check_state(card):
    """Every reason this state card cannot be contributed."""
    out = shape_errors(card, STATE_SCHEMA)
    if out:
        return out
    out += common_errors(card, STATE_SCHEMA)
    sizes = names(BUCKETS)
    for part in card.get("parts", []):
        out += [] if part.get("size") in sizes else ["a part's size is not a bucket"]
        out += [] if part.get("language") in LANGUAGES else ["a part's language is not one of: " + ", ".join(LANGUAGES)]
        out += ["%s is not a measure a part may hold" % k for k in part.get("measures", {}) if k not in MEASURES]
    out += [] if card.get("parts") else ["card has no part"]
    for field, allowed in (("size", names(BUCKETS)), ("age", names(AGES)), ("commits", names(COMMITS)),
                           ("feels", FEELS), ("ai", AI)):
        out += [] if card.get(field) in allowed else ["%s is not one of: %s" % (field, ", ".join(allowed))]
    history = card.get("history", {})
    out += ["%s is not a gate's name" % k for k in history.get("gates", {}) if not re.fullmatch(GATE_NAME, k)]
    out += [] if history.get("refused", 0) <= history.get("commits", 0) else ["more commits refused than replayed"]
    return out


def check(card):
    """Every reason this card cannot be contributed."""
    if isinstance(card, dict) and card.get("kind") == "state":
        return check_state(card)
    out = shape_errors(card, SCHEMA)
    if out:
        return out
    out += common_errors(card, SCHEMA)
    out += [] if card.get("size") in [name for _, name in BUCKETS] else ["size is not a bucket"]
    out += [] if card.get("language") in LANGUAGES else ["language is not one of: " + ", ".join(LANGUAGES)]
    out += [] if card.get("runner") in RUNNERS + ("other",) else ["runner is not a known kind"]
    out += [] if card.get("status") in ("complete", "stopped") else ["status is not complete or stopped"]
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


LABELS = (("complexity_per_kloc", "functions over the limit", " per 1k"), ("over_cc_per_kloc", "complexity over it", " per 1k"),
          ("over_lines_per_kloc", "length over it", " per 1k"), ("escapes_per_kloc", "escapes", " per 1k"),
          ("test_ratio", "test lines per line", ""), ("assertions_per_test", "assertions per test", ""),
          ("unchecked_share", "tests checking nothing", "%"))
BREAKS = {"easy": "rarely", "mixed": "sometimes", "breaks": "often", "unsaid": "unsaid"}
REFERENCE = os.path.join(os.path.dirname(os.path.dirname(HERE)), "quality", "reference.json")


def standing(language, measure, value):
    """" (pNN)" for a measure the reference sample holds for the language, else nothing."""
    try:
        with open(REFERENCE) as handle:
            sample = json.load(handle)["languages"][language][measure]
    except (OSError, ValueError, KeyError):
        return ""
    return " (p%d)" % round(100 * sum(1 for v in sample if v < value) / len(sample)) if sample else ""


def part_text(part):
    shown = []
    for key, label, unit in LABELS:
        if key in part["measures"]:
            value = part["measures"][key] * (100 if unit == "%" else 1)
            shown.append("%s %s%s%s" % (label, ("%.2f" % value).rstrip("0").rstrip("."), unit,
                                        standing(part["language"], key, part["measures"][key])))
    return "%s, %s lines: %s" % (part["language"], part["size"], " · ".join(shown))


def text(got):
    """A state card in words: what a person reads, and what `take` reads back."""
    history = got["history"]
    gates = "".join(" · %s %d" % pair for pair in sorted(history["gates"].items(), key=lambda p: -p[1]))
    lines = ["cleat card %s · cleat %s" % (got["id"], got["cleat"][:10]),
             "%s lines · %s old · %s commits · AI wrote %s · changes break things %s · %s"
             % (got["size"], got["age"], got["commits"], got["ai"], BREAKS[got["feels"]],
                "used cleat before" if got["gated"] else "never used cleat")]
    lines += [part_text(part) for part in got["parts"]]
    lines += ["duplicated lines %s%%" % got["duplicated"],
              "last %d commits: %d refused%s" % (history["commits"], history["refused"], gates)]
    lines += ["note: %s" % got["note"]] if got["note"] else []
    return "\n\n".join(lines + ["pNN: the share of open-source projects in that language with less; lower is better, except for tests."])


def number(word):
    value = float(word)
    return int(value) if value == int(value) else value


def part_from(line):
    head, _, rest = line.partition(" lines: ")
    language, _, size = head.partition(", ")
    measures = {}
    for key, label, unit in LABELS:
        found = re.search(re.escape(label) + r" (-?[0-9.]+)", rest)
        if found:
            measures[key] = round(float(found.group(1)) / (100 if unit == "%" else 1), 4)
    return dict(language=language, size=size, measures=measures)


def from_text(pasted):
    """The state card a `text` form holds, or None when the pasted text is not one."""
    lines = [line.strip() for line in pasted.splitlines() if line.strip()]
    top = next((i for i, line in enumerate(lines) if re.match(r"cleat card [0-9a-f]{12} · cleat [0-9a-f]{7,40}$", line)), None)
    if top is None or len(lines) < top + 2:
        return None
    words = lines[top].split()
    about = [field.strip() for field in lines[top + 1].split(" · ")]
    if len(about) != 6:
        return None
    commit = subprocess.run(["git", "-C", HERE, "rev-parse", "--verify", "-q", words[-1] + "^{commit}"],
                            capture_output=True, text=True).stdout.strip()
    got = dict(schema=1, kind="state", id=words[2], cleat=commit or words[-1], note="",
               size=about[0][:-len(" lines")], age=about[1][:-len(" old")], commits=about[2][:-len(" commits")],
               ai=about[3][len("AI wrote "):], gated=about[5] == "used cleat before",
               feels={v: k for k, v in BREAKS.items()}.get(about[4][len("changes break things "):], about[4]),
               duplicated=0, parts=[], history=dict(commits=0, refused=0, gates={}))
    for line in lines[top + 2:]:
        if " lines: " in line and not line.startswith(("last ", "note: ")):
            got["parts"].append(part_from(line))
        elif line.startswith("duplicated lines "):
            got["duplicated"] = number(line[len("duplicated lines "):].rstrip("%"))
        elif line.startswith("note: "):
            got["note"] = line[len("note: "):]
        elif re.match(r"last \d+ commits: \d+ refused", line):
            counts = [field.strip() for field in line.split(" · ")]
            first = re.match(r"last (\d+) commits: (\d+) refused", counts[0])
            gates = {name: int(count) for name, count in (field.rsplit(" ", 1) for field in counts[1:])}
            got["history"] = dict(commits=int(first.group(1)), refused=int(first.group(2)), gates=gates)
    return got


def cleat_commit():
    done = subprocess.run(["git", "-C", HERE, "rev-parse", "HEAD"], capture_output=True, text=True)
    return done.stdout.strip()


def part_row(part):
    language = part.get("language") if part.get("language") in LANGUAGES else "other"
    measures = {k: round(part[k], 3) for k in MEASURES if isinstance(part.get(k), NUM)}
    return dict(language=language, size=bucket(part.get("lines", 0)), measures=measures)


def history_counts(path):
    """Commits replayed, commits refused and refusals by gate from history.py's file; zeros without one."""
    if not path:
        return dict(commits=0, refused=0, gates={})
    with open(path) as handle:
        rows = json.load(handle)
    gates = {}
    for row in rows:
        for name in row.get("failing") or []:
            if re.fullmatch(GATE_NAME, name):
                gates[name] = gates.get(name, 0) + 1
    return dict(commits=len(rows), refused=sum(1 for row in rows if row.get("failing")), gates=gates)


def carried_cleat(tree):
    """Whether the tree's last commit already holds a quality.json: cleat was attached before this run."""
    done = subprocess.run(["git", "-C", tree, "cat-file", "-e", "HEAD:quality.json"], capture_output=True)
    return done.returncode == 0


def repository_age(tree):
    """(age bucket, commits bucket) of the tree's history: first commit to last, and how many."""
    def git(*args):
        return subprocess.run(["git", "-C", tree, *args], capture_output=True, text=True).stdout.split()
    stamps = sorted(int(t) for t in git("log", "--format=%ct"))
    days = (stamps[-1] - stamps[0]) / 86400 if stamps else 0
    return bucket(days, AGES), bucket(len(stamps), COMMITS)


def state_card(tree, history=None, note="", feels="unsaid", ai="unsaid"):
    """The state card for a tree cleat is attached to: its summary, and history.py's counts if given."""
    done = subprocess.run([sys.executable, os.path.join("quality", "bin", "gate.py"), "--summary", "--json"],
                          cwd=tree, capture_output=True, text=True)
    if done.returncode:
        raise SystemExit("gate.py --summary failed in %s:\n%s" % (tree, done.stderr or done.stdout))
    summary = json.loads(done.stdout)
    duplicated = next((g["total"] for g in summary.get("gates", []) if g.get("check") == "duplication"), 0)
    age, commits = repository_age(tree)
    return dict(schema=1, kind="state", id=secrets.token_hex(6), cleat=cleat_commit(), note=note[:NOTE_MAX],
                size=bucket(summary.get("lines", 0)), age=age, commits=commits, feels=feels, ai=ai,
                gated=carried_cleat(tree),
                duplicated=duplicated, parts=[part_row(p) for p in summary.get("parts", [])],
                history=history_counts(history))


def issue_link(got):
    """A link that opens a new issue with the card already in it; empty when the card is too long for one."""
    for text in (json.dumps(got, indent=1), json.dumps(got, separators=(",", ":"))):
        body = "%s\n\n```json\n%s\n```\n\n%s\n" % (ISSUE_TOP, text, ISSUE_END)
        link = ISSUES + "?" + urllib.parse.urlencode({"title": "card %s" % got["id"], "body": body})
        if len(link) <= LINK_MAX:
            return link
    return ""


def save(got):
    """Write a card that passes the check; refuse one that does not."""
    problems = check(got)
    if problems:
        raise SystemExit("not shareable:\n  " + "\n  ".join(problems))
    os.makedirs(CONTRIBUTED, exist_ok=True)
    path = os.path.join(CONTRIBUTED, got["id"] + ".json")
    with open(path, "w") as handle:
        json.dump(got, handle, indent=1)
        handle.write("\n")
    return path


def write_card(work, upto=None, note="", got=None):
    got = got or card(os.path.abspath(work), upto, note)
    print(json.dumps(got, indent=1))
    path = save(got)
    print("\nwrote %s: this is everything it shares. Read it." % path)
    if got.get("kind") == "state":
        print("\nTo share it as a comment, paste these lines:\n\n%s\n" % text(got))
    link = issue_link(got)
    if link:
        print("To share it, open this link and press Submit (nothing is sent until you do):\n%s" % link)
        print("or let this open it in your browser: python3 %s open %s" % (os.path.abspath(__file__), path))
    print("Or open a pull request with that one file.")


def take(pasted):
    """The card inside pasted text, checked and written: its fenced block, or from the first brace to the last."""
    fenced = FENCED.search(pasted)
    start, end = pasted.find("{"), pasted.rfind("}")
    worded = None if fenced else from_text(pasted)
    if worded:
        print("wrote %s" % save(worded))
        return
    if not fenced and (start < 0 or end < start):
        raise SystemExit("no card found in the text")
    try:
        got = json.loads(fenced.group(1) if fenced else pasted[start:end + 1])
    except ValueError as err:
        raise SystemExit("the card is not JSON: %s" % err)
    print("wrote %s" % save(got))


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
    if argv[:1] == ["open"] and len(argv) == 2:
        with open(argv[1]) as handle:
            link = issue_link(json.load(handle))
        import webbrowser
        print("opening the issue form; nothing is sent until you press Submit" if link and webbrowser.open(link)
              else "could not open a browser; paste the card's contents into a new issue instead")
        return 0
    if argv[:1] == ["text"] and len(argv) == 2:
        with open(argv[1]) as handle:
            print(text(json.load(handle)))
        return 0
    if argv[:1] == ["take"]:
        take(open(argv[1]).read() if len(argv) > 1 else sys.stdin.read())
        return 0
    if argv[:1] in (["card"], ["state"]) and len(argv) >= 2:
        flags = ("--note", "--feels", "--ai")
        said = {f: argv[argv.index(f) + 1] for f in flags if f in argv[:-1]}
        rest = [a for i, a in enumerate(argv[1:], 1) if a not in flags and argv[i - 1] not in flags]
        second, note = (rest[1] if len(rest) > 1 else None), said.get("--note", "")
        made = None
        if argv[0] == "state":
            made = state_card(os.path.abspath(rest[0]), second, note, said.get("--feels", "unsaid"), said.get("--ai", "unsaid"))
        write_card(rest[0], second, note, made)
        return 0
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

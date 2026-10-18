"""summary — the debt the baselines record, how heavy it is, and which way it moved.

`gate.py --summary` reads every baselined gate's file and says how much debt
each one records: functions over the complexity ceiling, escape and convention
sites, scanner results, the duplicated share, functions whose complexity the
tests do not pay for. It runs no gate and needs no tool, so it answers "what
state is this codebase in" in seconds. The numbers are what the baselines
record: exact after a `--strict` or `--tighten` run, an upper bound before one.

Each row carries what makes a count comparable and actionable:

  per 1k lines  the debt against the non-blank lines of the code that gate reads
  over          how far past the line the debt goes — cyclomatic and lines over
                the complexity ceilings, CRAP over the threshold — so a function
                cut from 35 to 20 is progress before it is under the gate
  changing      how much of it sits in files a commit touched in the churn window
                (`hotspots.window_days`, default 90): the debt being paid interest
                on, as against debt nobody opens
  trend         against the same file in git — the commit that first added it
                (adoption day), or with `--since 30d` the last commit before then,
                adoption day again for a baseline younger than that

and the summary ends with what is not measured: a debt gate the config does not
have, and production code in a language the complexity or escapes gate does
not read. A count with nothing behind it reads as clean.

There is no combined grade; the only score cleat gives is a count that goes
down. public-api and inventory are left out: what they record is surface, not
debt. `--json` prints the same as one object.
"""

import json
import os
import re
import subprocess

import ratchet
import reference
from extractors import census as census_
from extractors import churn
from extractors import parts as parts_

# check → what one unit of its debt is
DEBT = {
    "complexity": "function(s) over the complexity ceiling",
    "crap": "function(s) whose complexity the tests do not pay for",
    "escapes": "escape site(s)",
    "conventions": "convention site(s)",
    "sarif": "scanner result(s)",
    "duplication": "of significant lines duplicated",
}
# A debt gate a config can lack, and what goes untracked without it.
EXPECTED = {
    "complexity": "functions growing past a complexity ceiling are not tracked",
    "escapes": "type escapes, silenced lints and skipped tests are not tracked",
    "duplication": "copied blocks are not tracked",
    "crap": "complex code the tests do not run is not tracked (it needs a coverage report)",
    "sarif": "no scanner's results are ratcheted (Semgrep, CodeQL, gitleaks… write SARIF)",
    "exclusions": "a header saying a tool wrote a file takes it out of these counts unchecked",
}
LIZARD_LANGUAGES = {"python", "typescript", "swift", "rust", "kotlin", "java", "go", "ruby"}
# Not production: tests, and the examples, docs, benchmarks and demos a repository carries beside its code
TEST_DIR_RE = re.compile(r"^(?:tests?|specs?|__tests__|[A-Za-z]+Tests|testing|testdata|e2e)$", re.IGNORECASE)
ASIDE_DIR_RE = re.compile(r"^(?:examples?|samples?|demos?|docs?|docs_src|documentation|website|benchmarks?|bench|"
                          r"profiling|playgrounds?|[\w.-]+\.playground|[\w.-]+\.docc|stories)$", re.IGNORECASE)
TEST_FILE_RE = re.compile(r"(?:^test_|_test\.|\.test\.|\.spec\.|Tests?\.swift$)")
CENSUS_SKIP = {".claude", ".github", ".idea", ".vscode"}
GAP_MIN_LINES = 200      # a language this small is not worth a line
GAP_MIN_SHARE = 0.2      # nor is a gate that reads most of one


# ---------------------------------------------------------------- one baseline

def total(check, entries):
    """The one number a row reports: entries for a gate over functions, the summed
    counts for a gate over sites, the share for duplication."""
    if check == "duplication":
        return round(sum(e.get("percent", 0) for e in entries), 2)
    if check in ("complexity", "crap"):
        return len(entries)
    return sum(e.get("count", 1) for e in entries)


def over(check, entries, spec):
    """How far past its line the debt goes: {"cc", "lines"} over the complexity
    ceilings, {"crap"} over the CRAP threshold; {} for a gate over sites."""
    if check == "complexity":
        ceilings = spec.get("ceilings") or {}
        cc, n = ceilings.get("cc", 8), ceilings.get("lines", 60)
        return {"cc": sum(max(0, e.get("cc", 0) - cc) for e in entries),
                "lines": sum(max(0, e.get("lines", 0) - n) for e in entries)}
    if check == "crap":
        threshold = float(spec.get("threshold", 8))
        return {"crap": round(sum(max(0.0, e.get("crap", 0) - threshold) for e in entries), 1)}
    return {}


def _complexity(entries):
    return "worst cc %d, longest %d lines" % (max(e.get("cc", 0) for e in entries), max(e.get("lines", 0) for e in entries))


def _crap(entries):
    return "worst CRAP %s" % max(e.get("crap", 0) for e in entries)


def _duplication(entries):
    return "%d of %d lines" % (sum(e.get("duplicated_lines", 0) for e in entries), sum(e.get("total_lines", 0) for e in entries))


DETAILS = {"complexity": _complexity, "crap": _crap, "duplication": _duplication,
           "escapes": lambda entries: _commonest(entries, "escape"),
           "conventions": lambda entries: _commonest(entries, "rule"),
           "sarif": lambda entries: ""}


def detail(check, entries):
    """What a row adds after its number: the worst function, or the commonest kinds."""
    return DETAILS[check](entries) if entries else ""


def _commonest(entries, key):
    counts = {}
    for e in entries:
        if e.get(key):
            counts[e[key]] = counts.get(e[key], 0) + e.get("count", 1)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join("%s %d" % kv for kv in ranked[:3]) + (", …" if len(ranked) > 3 else "")


def changing(check, entries, root, touched):
    """How much of the debt is in files a commit touched in the window; None without git."""
    if touched is None or check == "duplication":
        return None
    return total(check, [e for e in entries if os.path.realpath(os.path.join(root, e.get("file", ""))) in touched])


# ---------------------------------------------------------------- git

def _git(cwd, *args):
    try:
        proc = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _commit(cwd, name, before):
    """([hash, date], label) of the copy to compare with: the last before `before`, else the first."""
    found = _git(cwd, "log", "-1", "--before=%s" % before.isoformat(), "--format=%h %as", "--", name) if before else None
    if found:
        return found.split(), "before"
    added = _git(cwd, "log", "--diff-filter=A", "--format=%h %as", "--", name)
    return (added.splitlines()[-1].split() if added else None), "at adoption"


def _entries_at(cwd, rev, name):
    try:
        data = json.loads(_git(cwd, "show", "%s:./%s" % (rev, name)) or "")
    except ValueError:
        return None
    return data if isinstance(data, list) else list(data.get("entries", []))


def past(path, before=None):
    """(entries, date, label) for `path` as git last had it before `before` (a datetime),
    else — no `before`, or a baseline younger than it — as it was first committed; None
    when git has no copy at all."""
    cwd, name = os.path.dirname(path), os.path.basename(path)
    rev_date, label = _commit(cwd, name, before)
    entries = _entries_at(cwd, rev_date[0], name) if rev_date else None
    return None if entries is None else (entries, rev_date[1], label)


def touched_files(root, window_days):
    """The realpaths a commit touched in the last `window_days` days; None outside git."""
    top = _git(root, "rev-parse", "--show-toplevel")
    if top is None:
        return None
    try:
        return set(churn.commits_by_file(top, window_days))
    except churn.ChurnError:
        return None


# ---------------------------------------------------------------- what each gate reads

def _as_list(value):
    return [value] if isinstance(value, str) else list(value or [])


def _conventions_reads(spec):
    """Every rule's roots; its languages only when every rule names some (a rule by extension reads any)."""
    rules = spec.get("rules") or []
    roots = [r for rule in rules for r in _as_list(rule.get("roots"))]
    named = [rule.get("languages") for rule in rules]
    return roots, (sum(named, []) if named and all(named) else None)


def _complexity_reads(spec):
    langs = spec.get("languages")
    if not langs and spec.get("tool", "swiftlint") == "swiftlint":
        langs = ["swift"]
    return _as_list(spec.get("sources")), langs


def reads(check, spec):
    """(roots, languages or None, skip_dirs, exclude) of the code a gate reads; None when
    it names no roots (a scanner's report)."""
    if check == "crap":
        spec = spec.get("complexity") or spec
    if check == "conventions":
        roots, langs = _conventions_reads(spec)
    elif check in ("complexity", "crap"):
        roots, langs = _complexity_reads(spec)
    else:
        roots, langs = _as_list(spec.get("roots")), spec.get("languages")
    if not roots:
        return None
    return roots, langs, spec.get("skip_dirs", []), spec.get("exclude", [])


def read_files(census, check, spec):
    found = reads(check, spec)
    return None if found is None else census_.read_by(census, *found)


# ---------------------------------------------------------------- the whole

class Context:
    """What every row is measured against: the tree's census, its test files, the churn window."""

    def __init__(self, config, baselines, before, window_days):
        self.root = config.root
        self.before = before
        self.generated = {}
        self.census = census_.take(config.root, CENSUS_SKIP | _quality_skip(baselines), self.generated)
        self.tests = _test_files(self.census, config)
        self.production = set(self.census) - self.tests
        self.touched = touched_files(config.root, window_days)


def _quality_skip(baselines):
    """cleat's own copy under quality/ is not the project's code, unless a debt gate reads it."""
    roots = [r for _, check, _, spec in baselines for r in (reads(check, spec) or ([],))[0]]
    return set() if any(os.path.normpath(r).split(os.sep)[0] == "quality" for r in roots) else {"quality"}


def _is_test(rel):
    """Not production, by its name or a directory's: a test, or an example, doc, benchmark or
    demo. The hygiene section's roots are no guide, since Rust keeps its tests inline and a
    project lists `src` there."""
    dirs = rel.split("/")[:-1]
    return bool(TEST_FILE_RE.search(os.path.basename(rel)) or any(_test_dir(p) or ASIDE_DIR_RE.match(p) for p in dirs))


def _test_dir(name):
    return bool(TEST_DIR_RE.match(name))


def _aside(rel):
    """An example, doc, benchmark or demo — not a test, and not production either."""
    dirs = rel.split("/")[:-1]
    return any(ASIDE_DIR_RE.match(p) for p in dirs) and not (TEST_FILE_RE.search(os.path.basename(rel)) or any(_test_dir(p) for p in dirs))


def _test_files(census, config):
    return {rel for rel in census if _is_test(rel)}


def _measure(r, check, spec, entries, ctx):
    files = read_files(ctx.census, check, spec)
    size = census_.lines(ctx.census, files if files is not None else ctx.production)
    now = total(check, entries)
    r.update(total=now, detail=detail(check, entries), over=over(check, entries, spec), lines=size,
             per_kloc=None if check == "duplication" or not size else round(1000.0 * now / size, 2),
             changing=changing(check, entries, ctx.root, ctx.touched))


def row(name, check, path, spec, ctx):
    """One baselined gate, as data."""
    r = {"name": name, "check": check, "baseline": os.path.relpath(path, ctx.root), "unit": DEBT[check]}
    if not os.path.isfile(path):
        return dict(r, missing=True)
    entries, _ = ratchet.read(path)
    _measure(r, check, spec, entries, ctx)
    was = past(path, ctx.before)
    if was is not None:
        r["then"] = {"total": total(check, was[0]), "over": over(check, was[0], spec), "date": was[1],
                     "adoption": was[2] == "at adoption"}
    return r


def gaps(baselines, ctx):
    """The production code in a language the complexity or escapes gates do not read."""
    found = []
    for check in ("complexity", "escapes"):
        specs = [spec for _, c, _, spec in baselines if c == check]
        if specs:
            read = set().union(*[read_files(ctx.census, check, s) or set() for s in specs])
            found += _gaps_for(check, ctx.census, ctx.production, read)
    return found


def _gaps_for(check, census, production, read):
    have = census_.by_language(census, production)
    unread = production - read
    out = []
    for language, lines in sorted(census_.by_language(census, unread).items(), key=lambda kv: -kv[1]):
        readable = check != "complexity" or language in LIZARD_LANGUAGES
        if readable and lines >= GAP_MIN_LINES and lines >= GAP_MIN_SHARE * have[language]:
            where = _commonest_dir(census, [f for f in unread if census[f][0] == language])
            out.append({"check": check, "language": language, "unread": lines, "lines": have[language], "under": where})
    return out


def _commonest_dir(census, files):
    weight = {}
    for rel in files:
        top = rel.split("/")[0] if "/" in rel else "."
        weight[top] = weight.get(top, 0) + census[rel][1]
    return max(sorted(weight), key=weight.get) if weight else "."


def gate_entries(baselines):
    """[(check, entries, spec)] for every baseline file that exists."""
    return [(check, ratchet.read(path)[0], spec) for _, check, path, spec in baselines if os.path.isfile(path)]


def part_rows(config, baselines, ctx, table):
    """Each part's size, debt per 1k production lines, and standing against the reference."""
    found = parts_.detect(ctx.root, ctx.census, ctx.production, config.data.get("parts"), CENSUS_SKIP | {"quality"})
    entries = gate_entries(baselines)
    rows = []
    for name, roots in found:
        files = parts_.files_of(name, roots, ctx.production, found)
        m = parts_.metrics(ctx.census, files, parts_.debt(entries, files))
        m["standing"] = reference.standings(table, m)
        rows.append(dict(m, name=name, roots=roots))
    return rows


def left_out(ctx):
    """What the counts leave out: files marked generated, and examples, docs and benchmarks."""
    return {"generated_files": len(ctx.generated), "generated_lines": sum(n for _, n in ctx.generated.values()),
            "aside_lines": census_.lines(ctx.census, {f for f in ctx.tests if _aside(f)})}


def collect(config, baselines, before=None, window_days=90):
    """The summary as one JSON-ready object. `baselines` is [(gate name, check, path, spec)]."""
    ctx = Context(config, baselines, before, window_days)
    table = reference.load()
    configured = {check for _, check, _, _ in baselines} | set(config.data) | {g.get("check") for g in config.data.get("gates", [])}
    return {
        "lines": census_.lines(ctx.census, ctx.production),
        "test_lines": census_.lines(ctx.census, ctx.tests),
        "window_days": window_days if ctx.touched is not None else None,
        "gates": [row(name, check, path, spec, ctx) for name, check, path, spec in baselines],
        "not_configured": [{"check": c, "untracked": why} for c, why in EXPECTED.items() if c not in configured],
        "not_read": gaps(baselines, ctx),
        "parts": part_rows(config, baselines, ctx, table),
        "left_out": left_out(ctx),
        "reference": (table or {}).get("provenance"),
    }


# ---------------------------------------------------------------- the text

def _num(value):
    if isinstance(value, float):
        return "{:,.2f}".format(value).rstrip("0").rstrip(".")
    return "{:,}".format(value)


def _k(lines):
    return "%.1fk" % (lines / 1000.0) if lines >= 1000 else str(lines)


def trend(now, then, unit=""):
    if now == then:
        return "unchanged"
    return "%s %s%s" % ("down" if now < then else "up", _num(round(abs(now - then), 2)), unit)


OVER_WORDS = {"cc": "cyclomatic", "lines": "lines", "crap": "CRAP"}
OVER_LINE = {"complexity": "over the ceilings", "crap": "over the threshold"}


def _facts(r, window_days):
    facts = []
    if r.get("per_kloc") is not None:
        facts.append("%s per 1k lines of the %s it reads" % (_num(r["per_kloc"]), _k(r["lines"])))
    if any(r["over"].values()):
        facts.append("%s: %s" % (OVER_LINE[r["check"]], ", ".join("%s %s" % (_num(v), OVER_WORDS[k]) for k, v in r["over"].items())))
    if r.get("changing") is not None:
        facts.append("%s in files changed in the last %d days" % (_num(r["changing"]), window_days))
    return facts


def _then_text(r, unit):
    then = r.get("then")
    if then is None:
        return "no committed copy to compare with"
    when = "at adoption on %s" % then["date"] if then["adoption"] else "on %s" % then["date"]
    text = "%s from %s%s %s" % (trend(r["total"], then["total"], unit), _num(then["total"]), unit, when)
    if then["over"] and then["over"] != r["over"]:
        text += "; %s then: %s" % (OVER_LINE[r["check"]], ", ".join("%s %s" % (_num(v), OVER_WORDS[k]) for k, v in then["over"].items()))
    return text


def _row_lines(r, window_days):
    if r.get("missing"):
        return ["  %-16s no baseline file at %s" % (r["name"], r["baseline"])]
    unit = "%" if r["check"] == "duplication" else ""
    head = "  %-16s %s%s %s" % (r["name"], _num(r["total"]), unit, r["unit"]) + (" (%s)" % r["detail"] if r["detail"] else "")
    facts = _facts(r, window_days)
    return [head] + (["  %-16s %s" % ("", "; ".join(facts))] if facts else []) + ["  %-16s %s" % ("", _then_text(r, unit))]


PART_METRICS = (("complexity_per_kloc", "functions over"), ("over_cc_per_kloc", "cyclomatic over"), ("escapes_per_kloc", "escapes"))


def _measure_text(part, key, label):
    standing = part["standing"].get(key)
    text = "%s %s" % (label, _num(round(part[key], 2)))
    return text + (" (p%d of %d)" % (round(100 * standing[0]), standing[1]) if standing else "")


def _parts_head(ref):
    if not ref:
        return "Parts, per 1k production lines:"
    return ("Parts, per 1k production lines — pNN: the share of the parts of %d open-source repositories in the "
            "same language with less (measured %s):" % (ref["repos"], ref["measured"]))


def _part_line(p):
    measures = [_measure_text(p, k, label) for k, label in PART_METRICS if p.get(k) is not None]
    return "  %-16s %s, %s lines: %s" % (p["name"], p["language"], _k(p["lines"]), "; ".join(measures))


def _compared(parts):
    return any(p["standing"].get(k) for p in parts for k, _ in PART_METRICS)


def _part_lines(data):
    """A row per part — shown when there is more than one, or a reference to compare one with."""
    parts = [p for p in data.get("parts", []) if p["lines"]]
    if len(parts) < 2 and not _compared(parts):
        return []
    return [_parts_head(data.get("reference"))] + [_part_line(p) for p in parts]


def _left_out_lines(left):
    """What the counts above leave out, so a jump in it is seen: code moved into an example
    directory, or a header saying a tool wrote it (the exclusions gate ratchets those)."""
    if not left or not (left["generated_lines"] or left["aside_lines"]):
        return []
    return ["Left out of the counts: %s lines in %d file(s) marked generated; %s lines of examples, docs and benchmarks"
            % (_k(left["generated_lines"]), left["generated_files"], _k(left["aside_lines"]))]


def _unmeasured_lines(data):
    lines = ["  %-16s %s" % (n["check"], n["untracked"]) for n in data["not_configured"]]
    lines += ["  %-16s does not read %s of the %s lines of %s production code (most under %s/)"
              % (g["check"], _k(g["unread"]), _k(g["lines"]), g["language"], g["under"]) for g in data["not_read"]]
    return ["Not measured:"] + lines if lines else []


def render(data):
    """The summary as text lines."""
    if not data["gates"]:
        return ["cleat summary: no gate here keeps a baseline of debt (complexity, crap, escapes, conventions, sarif, duplication)."] + _unmeasured_lines(data)
    out = ["cleat summary: %s lines of production source, %s of tests, examples and docs — the debt each baseline records, "
           "exact after a --strict or --tighten run, an upper bound before one" % (_k(data["lines"]), _k(data["test_lines"]))]
    for r in data["gates"]:
        out += _row_lines(r, data["window_days"])
    out += _part_lines(data)
    out += _left_out_lines(data.get("left_out"))
    out += _unmeasured_lines(data)
    if any(r["check"] == "complexity" for r in data["gates"]):
        out.append("Where to start: quality/bin/report-hotspots.py ranks the functions by churn × complexity.")
    return out

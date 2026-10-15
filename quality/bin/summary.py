"""summary — the debt the baselines record, and which way it moved.

`gate.py --summary` reads every baselined gate's file and says how much debt
each one records: functions over the complexity ceiling, escape and convention
sites, scanner results, the duplicated share, functions whose complexity the
tests do not pay for. It runs no gate and needs no tool, so it answers "what
state is this codebase in" in a second. The numbers are what the baselines
record: exact after a `--strict` or `--tighten` run, an upper bound before one.

Each row is compared with the same file in git — by default the commit that
first added it (adoption day), or with `--since 30d` the last commit before
then, adoption day again for a baseline younger than that — so the summary says whether the debt is falling, which is the only
quality score cleat gives: a count that goes down. public-api and inventory
are left out; what they record is surface, not debt.
"""

import json
import os
import subprocess

import ratchet

# check → what one unit of its debt is
DEBT = {
    "complexity": "function(s) over the complexity ceiling",
    "crap": "function(s) whose complexity the tests do not pay for",
    "escapes": "escape site(s)",
    "conventions": "convention site(s)",
    "sarif": "scanner result(s)",
    "duplication": "of significant lines duplicated",
}


def total(check, entries):
    """The one number a row reports: entries for a gate over functions, the summed
    counts for a gate over sites, the share for duplication."""
    if check == "duplication":
        return round(sum(e.get("percent", 0) for e in entries), 2)
    if check in ("complexity", "crap"):
        return len(entries)
    return sum(e.get("count", 1) for e in entries)


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


def past(path, before=None):
    """(entries, date, label) for `path` as git last had it before `before` (a datetime),
    else — no `before`, or a baseline younger than it — as it was first committed; None
    when git has no copy at all."""
    cwd, name = os.path.dirname(path), os.path.basename(path)
    rev_date, label = _commit(cwd, name, before)
    if not rev_date:
        return None
    text = _git(cwd, "show", "%s:./%s" % (rev_date[0], name))
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return (data if isinstance(data, list) else list(data.get("entries", []))), rev_date[1], label


def trend(now, then, unit=""):
    if now == then:
        return "unchanged"
    return "%s %s%s" % ("down" if now < then else "up", _num(round(abs(now - then), 2)), unit)


def _num(value):
    return ("%.2f" % value).rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


def row(name, check, path, before=None):
    """The lines one baselined gate contributes."""
    if not os.path.isfile(path):
        return ["  %-16s no baseline file at %s" % (name, path)]
    entries, _ = ratchet.read(path)
    now = total(check, entries)
    unit = "%" if check == "duplication" else ""
    line = "  %-16s %s%s %s" % (name, _num(now), unit, DEBT[check])
    extra = detail(check, entries)
    if extra:
        line += " (%s)" % extra
    was = past(path, before)
    if was is None:
        return [line, "  %-16s no committed copy to compare with" % ""]
    entries_then, date, label = was
    then = total(check, entries_then)
    when = "at adoption on %s" % date if label == "at adoption" else "on %s" % date
    return [line, "  %-16s %s from %s%s %s" % ("", trend(now, then, unit), _num(then), unit, when)]


def report(baselines, before=None):
    """The summary as lines. `baselines` is [(gate name, check, baseline path)]."""
    if not baselines:
        return ["cleat summary: no gate here keeps a baseline of debt (complexity, crap, escapes, conventions, sarif, duplication)."]
    lines = ["cleat summary: the debt each baseline records — exact after a --strict or --tighten run, an upper bound before one"]
    for name, check, path in baselines:
        lines += row(name, check, path, before)
    if any(check == "complexity" for _, check, _ in baselines):
        lines.append("Where to start: quality/bin/report-hotspots.py ranks the functions by churn × complexity.")
    return lines

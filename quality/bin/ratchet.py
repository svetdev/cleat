"""ratchet — the one implementation of the baseline ratchet every gate shares.

A gate measures something and finds the places over its line. This module is
what happens next, and it is the same for every gate: read the baseline the
project accepted on adoption day, compare, and sort every finding into one of
five outcomes —

  new        over the line and not in the baseline               → FAIL
  worsened   in the baseline, and a ratcheted value went up      → FAIL
  held       in the baseline, no value went up                   → pass
  improved   in the baseline, and a ratcheted value came down    → pass, NOTE: tighten
  stale      in the baseline, matched nothing this run           → pass, NOTE: drop

The first two are what the ratchet refuses. The last two are the baseline
being looser than the code, which is a leak in the other direction: an
improved function could grow back to its old recorded value and pass. Locally
that is a NOTE with the command that tightens the file; under `--strict` —
what CI runs — it is a failure, so the baseline in the repository is always
exactly the debt that exists, never more.

A finding is keyed by its file and the text of its declaration line, so a
shifted line still matches, and carries every value the gate measured
(`{"cc": 9, "lines": 61}`, `{"cc": 6, "coverage": 0.25, "crap": 21.4}`). The
gate names which of those values ratchet — the ones where higher is worse.

The baseline records its provenance — the tool and version that measured it
and a hash of the gate's configuration — so a tool upgrade that shifts a
number, or a config change that widens what is judged, is noticed rather
than silently compared against. Both are NOTEs, and failures under
`--strict`.

The failure output says what fixes the code. It does not print the command
that accepts the debt: rewriting a baseline is a policy decision for a
person, and printing it beside the failure makes it the first thing an agent
reaches for. That command appears only in the NOTEs, where running it can
only tighten.

Baseline files are read in two shapes: the original bare list of entries, and
`{"provenance": {…}, "entries": […]}`, which is what `write()` produces.
"""

import hashlib
import json
import os


class Finding:
    """One place over a gate's line: where it is, the declaration text that keys it,
    and every value the gate measured there."""

    __slots__ = ("file", "line", "text", "values")

    def __init__(self, file, line, text, values):
        self.file = file
        self.line = line
        self.text = text
        self.values = dict(values)

    @property
    def key(self):
        return (self.file, self.text)

    def entry(self):
        """The baseline entry for this finding — key first, its line (a tie-breaker between
        identical declarations, never part of the key), then every value."""
        out = {"file": self.file, "text": self.text, "line": self.line}
        out.update(self.values)
        return out


class Verdict:
    """The five outcomes of one run, plus any provenance drift."""

    def __init__(self):
        self.new = []        # [Finding]
        self.worsened = []   # [(Finding, entry)]
        self.held = []       # [(Finding, entry)]
        self.improved = []   # [(Finding, entry)]
        self.stale = []      # [entry]
        self.drift = None    # a sentence, when the baseline was written under another tool or config

    @property
    def failed(self):
        return bool(self.new or self.worsened)

    @property
    def loose(self):
        """Whether the baseline records more than the code has — what --strict refuses."""
        return bool(self.stale or self.improved or self.drift)


# ---------------------------------------------------------------- the file

def read(path):
    """(entries, provenance) from a baseline file; ([], None) when there is none."""
    if not os.path.isfile(path):
        return [], None
    with open(path) as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return data, None
    return list(data.get("entries", [])), data.get("provenance")


def write(path, findings, provenance):
    with open(path, "w") as handle:
        json.dump({"provenance": provenance, "entries": [f.entry() for f in findings]}, handle, indent=1)
        handle.write("\n")


def config_hash(config):
    """A short stable digest of the gate's configuration, so a baseline can say what it
    was written under."""
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def provenance(tool, version, config):
    """What a baseline records about how it was measured. `version` may be None when
    the tool was not run this time (a saved report was judged instead)."""
    return {"tool": tool, "version": version, "config": config_hash(config)}


def drift_between(stored, current):
    """A sentence when `stored` provenance disagrees with `current`; None when they agree
    or either side is unknown."""
    if not stored or not current:
        return None
    tool = current.get("tool")
    checks = [
        ("tool", "the baseline was measured by %s, this run by %s" % (stored.get("tool"), tool)),
        ("version", "the baseline was measured by %s %s, this run by %s" % (tool, stored.get("version"), current.get("version"))),
        ("config", "the baseline was written under a different gate configuration (%s, now %s)" % (stored.get("config"), current.get("config"))),
    ]
    for field, sentence in checks:
        if stored.get(field) and current.get(field) and stored[field] != current[field]:
            return sentence
    return None


def restrict(findings, entries, files):
    """The findings and entries for `files` only (repo-relative paths) — how a gate
    judges the files an agent changed without every other file's entry reading as stale."""
    if files is None:
        return findings, entries
    wanted = set(files)
    return [f for f in findings if f.file in wanted], [e for e in entries if e.get("file") in wanted]


def add_only_argument(parser):
    parser.add_argument("--only", nargs="*", metavar="FILE",
                        help="judge only these repo-relative files, against only their baseline entries (gate.py --changed passes the changed files)")


# ---------------------------------------------------------------- the judgment

def compare(finding, entry, metrics):
    """"worsened", "improved" or "held": how `finding` reads against its baseline `entry`
    on the ratcheted `metrics` — higher is worse; a value the entry did not record is not
    compared."""
    comparable = [m for m in metrics if m in entry and m in finding.values]
    if any(finding.values[m] > entry[m] for m in comparable):
        return "worsened"
    if any(finding.values[m] < entry[m] for m in comparable):
        return "improved"
    return "held"


def _affinity(finding, entry, metrics, f_index, e_index):
    """How well a finding fits an entry with the same (file, text): the more recorded
    values it still shares the better, then the nearer its line, then document order."""
    shared = sum(1 for m in metrics if m in entry and entry[m] == finding.values.get(m))
    distance = abs(entry["line"] - finding.line) if "line" in entry else 0
    return (-shared, distance, f_index, e_index)


def _assign(candidates, findings, entries):
    """Greedy assignment in affinity order: each finding and entry is paired at most once."""
    pairs, used_f, used_e = [], set(), set()
    for _score, i, j in candidates:
        if i not in used_f and j not in used_e:
            pairs.append((findings[i], entries[j]))
            used_f.add(i)
            used_e.add(j)
    return pairs, used_f, used_e


def _match_group(findings, entries, metrics):
    """Pair the findings and entries sharing one (file, text). Two `def check(` functions
    in one file are told apart by what they still share with their entry — an untouched
    function shares every value — then by nearest recorded line, so inserting a third
    between them leaves the inserted one unmatched, not one of its neighbours."""
    candidates = sorted((_affinity(f, e, metrics, i, j), i, j)
                        for i, f in enumerate(findings) for j, e in enumerate(entries))
    pairs, used_f, used_e = _assign(candidates, findings, entries)
    unmatched = [f for i, f in enumerate(findings) if i not in used_f]
    stale = [e for j, e in enumerate(entries) if j not in used_e]
    return pairs, unmatched, stale


def judge(findings, entries, metrics, stored_provenance=None, current_provenance=None):
    """Sort `findings` against the baseline `entries` into the five outcomes. Identical
    `(file, text)` can repeat within a run; `_match_group` tells them apart."""
    verdict = Verdict()
    groups = {}
    for e in entries:
        groups.setdefault((e["file"], e["text"]), ([], []))[1].append(e)
    for f in findings:
        groups.setdefault(f.key, ([], []))[0].append(f)
    for key in sorted(groups, key=lambda k: min([f.line for f in groups[k][0]] or [0])):
        group_findings, group_entries = groups[key]
        pairs, unmatched, stale = _match_group(group_findings, group_entries, metrics)
        for finding, entry in sorted(pairs, key=lambda fe: fe[0].line):
            getattr(verdict, compare(finding, entry, metrics)).append((finding, entry))
        verdict.new += unmatched
        verdict.stale += stale
    verdict.new.sort(key=lambda f: (f.file, f.line))
    verdict.drift = drift_between(stored_provenance, current_provenance)
    return verdict


# ---------------------------------------------------------------- the report

class Gate:
    """What a gate says about itself when the engine prints for it.

    noun   — what a finding is: "production function(s)"
    over   — the line: "over the complexity gate (cyclomatic > 8 or body > 60 lines)"
    fix    — the sentence that says what fixes the code; never the accept command
    remedy — the command that rewrites the baseline; printed only where it can only tighten
    show   — values → "cc 9, 61 lines", for a finding line
    brief  — values → the same for a baseline entry (default: `show`)
    """

    def __init__(self, noun, over, fix, remedy, show, brief=None):
        self.noun = noun
        self.over = over
        self.fix = fix
        self.remedy = remedy
        self.show = show
        self.brief = brief or show


def _print_failures(verdict, gate, baseline_size, context):
    if verdict.new:
        print("FAIL: %d new %s %s, beyond the %d the baseline holds:"
              % (len(verdict.new), gate.noun, gate.over, baseline_size))
        for line in context:
            print("  %s" % line)
        for f in verdict.new:
            print("  %s:%d  %s  %s" % (f.file, f.line, gate.show(f.values), f.text[:70]))
    if verdict.worsened:
        print("FAIL: %d baselined %s got worse — the ratchet only tightens:"
              % (len(verdict.worsened), gate.noun))
        for line in context if not verdict.new else ():
            print("  %s" % line)
        for f, e in verdict.worsened:
            print("  %s:%d  %s, was %s  %s" % (f.file, f.line, gate.show(f.values), gate.brief(e), f.text[:70]))
    print(gate.fix)


def _print_listed(heading, rows):
    """A NOTE heading and up to 20 of its rows, with the count of the rest."""
    print(heading)
    for row in rows[:20]:
        print("  %s" % row)
    if len(rows) > 20:
        print("  … and %d more" % (len(rows) - 20))


def _print_notes(verdict, gate, offer_remedy):
    """Every way the baseline is looser than the code — and, on a passing run only, the
    command that tightens it. Beside a failure that same command would also accept the
    new debt, so there it is not printed."""
    if verdict.stale:
        n = len(verdict.stale)
        _print_listed("NOTE: %d baseline entr%s matched nothing this run — fixed, split, renamed or deleted:"
                      % (n, "y" if n == 1 else "ies"),
                      ["%s  %s  %s" % (e["file"], gate.brief(e), e["text"][:70]) for e in verdict.stale])
    if verdict.improved:
        _print_listed("NOTE: %d baselined %s improved — the baseline still records the old value:"
                      % (len(verdict.improved), gate.noun),
                      ["%s:%d  %s, baseline says %s  %s" % (f.file, f.line, gate.show(f.values), gate.brief(e), f.text[:70])
                       for f, e in verdict.improved])
    if verdict.drift:
        print("NOTE: %s — its numbers may not be comparable." % verdict.drift)
    if verdict.loose and offer_remedy:
        print("Tighten the baseline (this only ever lowers it): %s" % gate.remedy)


def report(verdict, gate, baseline_size, ok_line, quiet=False, strict=False, context=()):
    """Print the verdict the way every gate prints it, and return the exit code:
    1 when something is new or worse (or, under --strict, when the baseline is loose),
    else 0. `ok_line` is the gate's success sentence; `context` lines follow the FAIL
    header (what was read, say)."""
    if verdict.failed:
        _print_failures(verdict, gate, baseline_size, context)
        _print_notes(verdict, gate, offer_remedy=False)
        return 1
    if not quiet:
        print(ok_line)
    _print_notes(verdict, gate, offer_remedy=True)
    if strict and verdict.loose:
        print("FAIL: the baseline is looser than the code — under --strict it must match exactly. "
              "Tighten it with the command above and commit the result.")
        return 1
    return 0


def add_strict_argument(parser):
    parser.add_argument("--strict", action="store_true",
                        help="fail when the baseline is looser than the code (stale or improved entries, "
                             "or a baseline measured under another tool or config) — what CI runs")

#!/usr/bin/env python3
"""check-crap — fail on a production function whose complexity is not paid for by tests.

CRAP (Change Risk Anti-Patterns) is the score Uncle Bob runs over agent-written
code: for a function with cyclomatic complexity `cc` and test coverage `cov`,

    crap = cc² × (1 − cov)³ + cc

A fully covered function scores its complexity; an uncovered one scores about
its complexity squared. So a simple uncovered function passes, a complex
covered one passes, and a complex uncovered one does not — which is the one
an agent writes when it is in a hurry, and the one nobody wants to touch
later. The complexity gate (check-complexity.py) catches complexity alone;
this is the half that asks whether the paths are exercised.

Complexity comes from SwiftLint, run with the threshold at 1 so every function
with more than one path is reported with its number; a function it does not
report has cc 1 and a CRAP of at most 2. Coverage comes from the last test
run, from two readers: the `.xcresult` bundle xcodebuild wrote, read with
`xcrun xccov`, for an app target; and the llvm-cov JSON export `swift test
--enable-code-coverage` writes, for a package. A function is matched between
a reader and SwiftLint by file and declaration line.

The SwiftLint and xcrun children each run within a wall-clock ceiling,
`SWIFTLINT_TIMEOUT_SECONDS` and `XCCOV_TIMEOUT_SECONDS` (default 600s each,
overridable via the environment variables of the same name) — the same shape
`extractors/complexity.py` uses for `LIZARD_TIMEOUT_SECONDS`. A child that runs past
its ceiling ends as a FAIL naming the tool and the limit instead of blocking
the postflight forever.

It is a ratchet, like the others: the functions over the gate when it was
written are in the baseline file, keyed by file and the text of their
declaration line so a shifted line still matches; a new one fails. The success
line prints the counts. It reads the *last* run's coverage, so it is run after
the suite rather than before it — a postflight — and by hand:

  quality/bin/check-crap.py                       # newest bundle, newest package export
  quality/bin/check-crap.py --write-baseline      # accept what is over the gate today
  quality/bin/check-crap.py --xccov F --codecov F --lint F [--baseline F]   # the tests use these
  quality/bin/check-crap.py --bundle PATH         # score this .xcresult, not the newest on the machine
  quality/bin/check-crap.py --estimate [--only F…]   # what gate.py's preflight runs: WARN, never fail
  quality/bin/check-crap.py --estimate --fail --changed   # a merge's verify step: FAIL, exit 1

A postflight gate is too late for a branch: debt merged clean surfaces only when
the release runs coverage. So `gate.py`'s preflight also runs each CRAP gate with
`--estimate` — from the last coverage run on disk, over only the functions that run
has a record for (an older run cannot tell an uncovered function from a moved one),
under `--changed` only the changed files. What would fail is a WARN; the exit is
0; with no coverage run on disk it says nothing. With `--fail` the same estimate is
a verdict — for an autopilot that merges item by item while the full suite cannot
run: a new or worse function is a FAIL and exit 1, and a coverage run it cannot
read is exit 2, not silence. `--changed` scopes it to the files changed against the
base (`--base REF` to name one), as `--only` would with the list spelled out.

The gate itself refuses a report a glob found when it is older than a file it would
judge: that is a leftover — an earlier toolchain's output directory, say — not the
run that just happened, and read anyway it scores every function as it stood then.
A report named with a flag is the run the caller chose, and is read as it is. The
estimate states its report's age and never refuses; that is what it is for.

A report written in another git worktree of the same repository — an autopilot's
item worktree reading the main checkout's coverage run — names paths that are not
this checkout's. When nothing it names is under the root, the other worktrees are
mapped onto this one and it is read again, with a NOTE; no path_map is needed.

Everything that names the project is the `crap` section of `quality.json`,
found by walking up from the working directory or named with `--config` (see
quality_config.py). Paths in it are relative to the file's directory, and
every path this prints or stores in the baseline is relative to that same
directory:

Swift packages. A package the app links is compiled into the app, and xccov
records the package's files in the app's `.xcresult` at 0% — the app target
did not instrument them. So the `xccov` reader keeps only files under its
`sources` root, prints a NOTE naming what it dropped, and the package's
coverage comes from a second reader: `llvm_cov` over the export the package's
own tests write (`swift test --enable-code-coverage`, or the `.profdata` under
DerivedData exported with `xcrun llvm-cov export`), with `sources` at the
package's source root. A package-backed app configures both readers.

  "crap": {
    "threshold": 8,                                  # the gate
    "baseline":  "Acme/.crap-baseline.json",     # the ratchet
    "sources":   ["Acme/Acme", "…/Sources/AcmeCore"],   # SwiftLint runs over these
    "xccov":    {"sources": "Acme/Acme",     # the root whose functions the xccov reader keeps
                 "bundles": "~/Library/Developer/Xcode/DerivedData/*/Logs/Test/*.xcresult"},  # newest wins
    "llvm_cov": {"sources": "…/Sources/AcmeCore",   # the root the llvm-cov reader keeps
                 "path_map": {"/work/": "."},        # any reader: a prefix the report uses → this checkout
                 "exports": ["…/.build/out/Products/Debug/codecov/AcmeCore.json",   # Swift 6.4 and later
                             "…/.build/*/debug/codecov/AcmeCore.json"]}               # earlier; newest match wins
  }

Every `exports` and `bundles` key takes one glob or a list; the newest match across
them is read.

A flag overrides its key — `--threshold`, `--baseline`, `--app-sources` (the
xccov root), `--package-sources` (the llvm-cov root), `--repo` (what paths
are reported relative to) — and the config is only opened for a value no flag
supplied, so a fully flagged run needs no quality.json at all. A key the
config lacks fails naming the key.
"""

import argparse
import json
import os
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
import ratchet
from extractors import changed, languages, complexity as complexity_readers
from extractors import coverage as coverage_reports

SECTION = "crap"


class Settings:
    """The project facts: each from its flag when given, else from the `crap` section of
    quality.json — opened the first time a value has to come from it."""

    def __init__(self, explicit_config):
        self.explicit = explicit_config
        self._config = None
        self.globbed = []   # [(key, globs key, report, root)] — each report a glob found, for refuse_stale
        self.quiet = False

    @property
    def config(self):
        if self._config is None:
            self._config = quality_config.load(self.explicit)
        return self._config

    @property
    def root(self):
        return self.config.root

    gate = None  # the name of the gate being run, when `crap` is a list of gates

    @property
    def section(self):
        """The `crap` object to read: the whole section, or — when it is a list of
        gates, each with a "name" — the one `gate` names."""
        raw = self.config.section(SECTION)
        if isinstance(raw, list):
            names = [g.get("name") for g in raw]
            if self.gate is None:
                raise KeyError("%s: \"crap\" is a list of gates (%s) — name one with --gate"
                               % (self.config.file, ", ".join(str(n) for n in names)))
            for g in raw:
                if g.get("name") == self.gate:
                    return g
            raise KeyError("%s: \"crap\" has no gate named \"%s\" (have: %s)"
                           % (self.config.file, self.gate, ", ".join(str(n) for n in names)))
        return raw

    def has(self, *keys):
        """Whether the section carries `keys` — a reader whose key is absent is not run.
        With no quality.json in reach (a fully flagged run) nothing is configured."""
        if self._config is None and self.explicit is None and quality_config.find() is None:
            return False
        value = self.section  # a list of gates with no --gate is that error, not "absent"
        for key in keys:
            if not isinstance(value, dict) or key not in value:
                return False
            value = value[key]
        return True

    def value(self, flag, *keys):
        """`flag` when given; else the config value at `keys` inside the section."""
        if flag is not None:
            return flag
        section = self.section
        if keys[0] not in section or section[keys[0]] is None:
            raise KeyError("%s: \"%s\" has no \"%s\" — see quality.example.json"
                           % (self.config.file, SECTION, keys[0]))
        value = section[keys[0]]
        for key in keys[1:]:
            if not isinstance(value, dict) or key not in value:
                raise KeyError("%s: \"%s\" has no \"%s\" — see quality.example.json"
                               % (self.config.file, SECTION, ".".join(keys)))
            value = value[key]
        return value

    def path(self, flag, *keys):
        """An absolute path: the flag as given, else the config's, resolved against its directory."""
        return os.path.abspath(flag) if flag is not None else self.config.path(self.value(None, *keys))


def crap(cc, coverage):
    """The score for complexity `cc` and coverage `coverage` in [0, 1]."""
    return cc * cc * (1.0 - coverage) ** 3 + cc


# ---------------------------------------------------------------- the judgement

def _from_spans(coverage, path, line, end):
    """What an istanbul-shaped reader knows beyond its listed functions: the statements
    in the function's own range, then the innermost listed function holding the line."""
    if not hasattr(coverage, "over"):
        return None
    cov = coverage.over(path, line, end) if end else None
    return cov if cov is not None else coverage.within(path, line)


def coverage_at(coverage, path, line, end=None):
    """The coverage for the declaration at `line`, 0.0 when nothing recorded it (see
    `coverage_record`)."""
    cov = coverage_record(coverage, path, line, end)
    return 0.0 if cov is None else cov


def coverage_record(coverage, path, line, end=None):
    """The coverage recorded for the declaration at `line`: a record on its own line; else,
    for a function the report never listed (a nested arrow function or closure lizard
    enumerates but istanbul folds into its parent), the statements in its own range or
    the function holding it; else a record on an attribute line above or the line a
    multi-line signature opens the body on (SwiftLint reports the `func` line, xccov
    records the function elsewhere); None when nothing recorded anything."""
    cov = coverage.get((path, line))
    if cov is not None:
        return cov
    cov = _from_spans(coverage, path, line, end)
    if cov is not None:
        return cov
    for candidate in coverage_reports.nearby_declaration_lines(path, line):
        if (path, candidate) in coverage:
            return coverage[(path, candidate)]
    return None


def judge(complexities, coverage, threshold, repo, ends=None, recorded_only=False):
    """[(file relative to `repo`, line, text, cc, cov, crap)] for every function over the gate.
    With `recorded_only`, a function the coverage run holds no record for is not judged —
    an estimate from an older run cannot tell an uncovered function from a moved one."""
    repo = os.path.realpath(repo)  # the files are realpaths; a symlinked repo must not relativise to ../../
    over = []
    for (path, line), cc in complexities.items():
        cov = coverage_record(coverage, path, line, (ends or {}).get((path, line)))
        if cov is None and recorded_only:
            continue
        score = crap(cc, cov or 0.0)
        if score > threshold:
            over.append((os.path.relpath(path, repo), line, complexity_readers.declaration_text(path, line), cc, cov or 0.0, score))
    over.sort(key=lambda o: (-o[5], o[0], o[1]))
    return over


class GateError(Exception):
    """A reason the gate cannot judge today; printed as FAIL, exit 2."""


def _saved_functions(path, settings):
    """A saved lizard run, kept to the gate's own sources — a run may cover every stack."""
    with open(path) as handle:
        functions, _ = complexity_readers.functions_from_csv(handle.read())
    if not settings.has("complexity", "sources"):
        return functions
    roots = [os.path.join(os.path.realpath(r), "") for r in settings.config.paths(settings.value(None, "complexity", "sources"))]
    return [f for f in functions if any(f.path.startswith(r) for r in roots)]


def functions_for(args, settings):
    """The measured functions, from --lizard-csv or lizard over `crap.complexity`; None
    when SwiftLint is the reader."""
    if args.lizard_csv:
        return _saved_functions(args.lizard_csv, settings)
    if settings.has("complexity", "tool") and settings.value(None, "complexity", "tool") == "lizard":
        spec = settings.value(None, "complexity")
        roots = narrowed(args, settings, settings.config.paths(spec["sources"]), lizard_suffixes(spec["languages"]))
        if not roots:
            return []
        text = complexity_readers.run_lizard(roots, spec["languages"], spec.get("exclude", []), root=settings.config.root)
        return complexity_readers.functions_from_csv(text, skip_rust_tests=spec.get("skip_rust_tests", True))[0]
    return None


def narrowed(args, settings, sources, suffixes):
    """`sources`, or under `--only` just the changed files inside them that the reader reads."""
    if args.only is None:
        return sources
    return complexity_readers.only_files(args.only, sources, suffixes, settings.config.path)


def lizard_suffixes(names):
    return languages.suffixes(names)


def complexities_for(args, settings):
    """({(file, line): cc}, {(file, line): end}) — from --lint (SwiftLint json, no ends),
    a saved lizard run, or the reader `crap.complexity.tool` names; SwiftLint otherwise."""
    if args.lint:
        with open(args.lint) as handle:
            return complexity_readers.complexities_from_swiftlint(json.load(handle)), {}
    try:
        functions = functions_for(args, settings)
        if functions is not None:
            return complexity_readers.complexities(functions), complexity_readers.ends(functions)
        roots = narrowed(args, settings, settings.config.paths(settings.value(None, "sources")), (".swift",))
        return (complexity_readers.swiftlint_complexities(roots) if roots else {}), {}
    except complexity_readers.ToolError as problem:
        raise GateError(str(problem))


def app_coverage(args, settings):
    """The xccov root's functions, and the file they were read from: from --xccov, a
    given --bundle, or else the newest bundle matching crap.xccov.bundles, through xcrun.
    A --bundle skips the glob entirely — it names the run, so nothing is guessed."""
    root = settings.path(args.app_sources, "xccov", "sources")
    if args.xccov:
        with open(args.xccov) as handle:
            report, source = json.load(handle), args.xccov
    else:
        bundle = args.bundle or newest_report(settings, "xccov", "bundles", root,
                                              "no .xcresult bundle matches %s — run the suite first")
        report, source = coverage_reports.read_xccov_bundle(bundle), bundle
    note_package_files(coverage_reports.xccov_package_files(report, root, path_map_for(settings, "xccov")), args, settings)
    return mapped(settings, "xccov", lambda path_map: coverage_reports.from_xccov(report, root, path_map)), source


def note_package_files(dropped, args, settings):
    """xccov records a linked Swift package's files in the app bundle at 0%; the reader
    drops them, and unless an llvm-cov reader is configured for the package those
    functions are judged with no coverage at all. Say so, once, loudly."""
    if not dropped or args.codecov or settings.has("llvm_cov"):
        return
    print("NOTE: the xccov report names %d Swift file(s) outside the app root (%s, …) — a package the app links. "
          "xccov records them at 0%%, so they were dropped; read the package through \"llvm_cov\" with the Xcode "
          "products (see check-crap.py: Swift packages) or its functions are judged uncovered." % (len(dropped), dropped[0]))


def path_map_for(settings, key):
    """`<key>.path_map`: {prefix in the report: prefix on this machine}, values resolved
    against quality.json's directory — the report was written where the tests ran. Any
    reader may carry one; a fully flagged run has none."""
    if not settings.has(key, "path_map"):
        return {}
    raw = settings.value(None, key, "path_map")
    return {source: os.path.join(settings.config.path(target), "") for source, target in raw.items()}


def worktree_map(directory):
    """{every checkout of this repository: this one} — the other worktrees, and this one
    onto itself, longest first so a worktree nested in the main checkout is not read as
    the main checkout; {} outside git or with no other worktree."""
    top, others = changed.worktrees(directory)
    if not others:
        return {}
    return {os.path.join(path, ""): os.path.join(top, "") for path in sorted(others + [top], key=len, reverse=True)}


def mapped(settings, key, read):
    """`read(path_map)` under `<key>.path_map`. A report that names nothing under the
    root, read in a git worktree, is read again with the repository's other checkouts
    mapped onto this one: an autopilot's worktree judges a coverage run written in the
    main checkout with nothing to configure. Anything else that names nothing here is
    the loud refusal it always was."""
    path_map = path_map_for(settings, key)
    try:
        return read(path_map)
    except coverage_reports.NoneUnder:
        others = worktree_map(settings.root) if settings.has() else {}
        if not others:
            raise
    found = read(dict(path_map, **others))
    if not settings.quiet:
        print("NOTE: the %s report was written in another worktree of this repository; its paths were read as this one's." % key)
    return found


def newest_report(settings, key, globs_key, root, missing):
    """The newest file matching `<key>.<globs_key>` — one glob or a list — remembered with
    the root it is read for, so the gate can refuse one older than the sources it judges.
    None matching is a GateError: `missing` with the globs."""
    patterns = settings.value(None, key, globs_key)
    patterns = [patterns] if isinstance(patterns, str) else list(patterns)
    found = coverage_reports.newest([settings.config.path(p) for p in patterns])
    if found is None:
        raise GateError(missing % ", ".join(patterns))
    settings.globbed.append((key, globs_key, found, root))
    return found


def package_coverage(args, settings):
    """The llvm-cov root's functions, and the file they were read from: from --codecov,
    else the newest export."""
    root = settings.path(args.package_sources, "llvm_cov", "sources")
    export = args.codecov or newest_report(settings, "llvm_cov", "exports", root,
                                           "no llvm-cov export matches %s — run the package's tests with --enable-code-coverage")
    with open(export) as handle:
        report = json.load(handle)
    return mapped(settings, "llvm_cov", lambda path_map: coverage_reports.from_codecov(report, root, path_map)), export


def web_coverage(args, settings):
    """The istanbul root's functions and the file they were read from: --istanbul, else
    the newest `crap.istanbul.exports` match."""
    root = settings.path(args.web_sources, "istanbul", "sources")
    export = args.istanbul or newest_report(settings, "istanbul", "exports", root,
                                            "no istanbul export matches %s — run the web tests with --coverage first")
    with open(export) as handle:
        report = json.load(handle)
    return mapped(settings, "istanbul", lambda path_map: coverage_reports.from_istanbul(report, root, path_map)), export


def report_coverage(args, settings, key, flag):
    """The LCOV or Cobertura root's functions and the file they were read from: the
    flag's file, else the newest `crap.<key>.exports` match; the root from
    --report-sources, else `crap.<key>.sources`."""
    root = settings.path(args.report_sources, key, "sources")
    path = flag or newest_report(settings, key, "exports", root, "no " + key + " report matches %s — run the tests with coverage first")
    base_dir = os.path.abspath(args.repo) if args.repo else (settings.root if settings.has() else os.path.dirname(os.path.abspath(path)))
    return mapped(settings, key, lambda path_map: coverage_reports.function_coverage(
        coverage_reports.read(path, base_dir, path_map), root)), path


# Where a reader's own tool says the file it just wrote is — named when the glob found an older one.
WHERE_WRITTEN = {
    "llvm_cov": " `swift test --show-codecov-path` prints where the last run wrote it; Swift 6.4 writes under "
                ".build/out/Products/Debug/codecov, with .build/debug a link to it.",
}


def stale_reports(settings, complexities):
    """[(key, globs key, report, [the judged files written after it])] for every report the
    globs found — a named one (--codecov, --bundle, …) is the run the caller chose."""
    files = {path for path, _ in complexities}
    out = []
    for key, globs_key, report, root in settings.globbed:
        under = os.path.join(os.path.realpath(root), "")
        written = os.path.getmtime(report)
        newer = sorted(p for p in files if p.startswith(under) and os.path.exists(p) and os.path.getmtime(p) > written)
        if newer:
            out.append((key, globs_key, report, newer))
    return out


def refuse_stale(settings, complexities, repo):
    """A GateError when a report the globs found is older than a file it would judge. Read
    anyway, it scores every function as it stood when it ran — a leftover from an earlier
    toolchain once read heavily tested code as 0% and named fifty functions that were not
    over the gate."""
    stale = stale_reports(settings, complexities)
    if not stale:
        return
    key, globs_key, report, newer = stale[0]
    repo = os.path.realpath(repo)
    raise GateError(
        "the %s report %s (%s old) is older than %d file(s) it would judge (%s%s) — it is not the last run's, "
        "and would score their functions as they were. Run the tests with coverage again; if they just ran, "
        "\"crap.%s.%s\" matches a leftover, not what the run wrote.%s"
        % (key, os.path.relpath(os.path.realpath(report), repo), _age(report), len(newer), os.path.relpath(newer[0], repo),
           ", …" if len(newer) > 1 else "", key, globs_key, WHERE_WRITTEN.get(key, "")))


# ---------------------------------------------------------------- the gate

def main():
    parser = argparse.ArgumentParser(description="CRAP over the configured sources, against a baseline")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--threshold", type=float, help="the gate (default: crap.threshold)")
    parser.add_argument("--xccov", help="an xccov --report --json file (default: the newest crap.xccov.bundles match, through xcrun)")
    parser.add_argument("--bundle", help="an .xcresult bundle to read through xcrun, skipping the crap.xccov.bundles glob (default: the newest match)")
    parser.add_argument("--codecov", help="an llvm-cov json export (default: the newest crap.llvm_cov.exports match)")
    parser.add_argument("--lint", help="a SwiftLint json report (default: run swiftlint over crap.sources)")
    parser.add_argument("--lizard-csv", help="a saved lizard --csv run to read complexity from")
    parser.add_argument("--istanbul", help="an istanbul coverage-final.json (default: the newest crap.istanbul.exports match)")
    parser.add_argument("--web-sources", help="the root the istanbul reader keeps (default: crap.istanbul.sources)")
    parser.add_argument("--lcov", help="an LCOV report (default: the newest crap.lcov.exports match)")
    parser.add_argument("--cobertura", help="a Cobertura XML report (default: the newest crap.cobertura.exports match)")
    parser.add_argument("--report-sources", help="the root the lcov/cobertura reader keeps (default: crap.<reader>.sources)")
    parser.add_argument("--gate", help="which gate to run when crap is a list of gates (default: the single section)")
    parser.add_argument("--baseline", help="the ratchet file (default: crap.baseline)")
    parser.add_argument("--app-sources", help="the root the xccov reader keeps (default: crap.xccov.sources)")
    parser.add_argument("--package-sources", help="the root the llvm-cov reader keeps (default: crap.llvm_cov.sources)")
    parser.add_argument("--repo", help="paths are reported relative to this (default: the directory of quality.json)")
    parser.add_argument("--estimate", action="store_true",
                        help="the preflight's look ahead: warn (never fail) on what would fail CRAP, from the last coverage run on disk")
    parser.add_argument("--fail", action="store_true",
                        help="with --estimate: a verdict, not a warning — exit 1 on a new or worse function, 2 when no coverage run can be read")
    parser.add_argument("--changed", action="store_true", help="judge only the files changed against the base (see --base)")
    parser.add_argument("--base", help="with --changed: the ref to diff against (default: the pull request's base, else the merge-base with main)")
    ratchet.add_only_argument(parser)
    ratchet.add_tighten_argument(parser)
    ratchet.add_strict_argument(parser)
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    settings = Settings(args.config)
    settings.gate, settings.quiet = args.gate, args.quiet
    try:
        scope_to_changes(args, settings)
        return estimate(args, settings) if args.estimate else gate(args, settings)
    except (GateError, KeyError, coverage_reports.CoverageError) as problem:
        print("FAIL: %s" % (problem.args[0] if problem.args else problem), file=sys.stderr)
        return 2


COVERAGE_READERS = (
    # (the flags that select it, its config key, its label, the reader)
    (("xccov", "bundle"), "xccov", "bundle", lambda args, settings: app_coverage(args, settings)),
    (("codecov",), "llvm_cov", "package export", lambda args, settings: package_coverage(args, settings)),
    (("istanbul",), "istanbul", "istanbul export", lambda args, settings: web_coverage(args, settings)),
    (("lcov",), "lcov", "lcov", lambda args, settings: report_coverage(args, settings, "lcov", args.lcov)),
    (("cobertura",), "cobertura", "cobertura", lambda args, settings: report_coverage(args, settings, "cobertura", args.cobertura)),
)


def gather_coverage(args, settings):
    """(coverage, what was read) from every reader the flags or the section configure."""
    coverage, sources_read = coverage_reports.Spanned(), []
    for flags, key, label, read in COVERAGE_READERS:
        if any(getattr(args, flag) for flag in flags) or settings.has(key):
            found, source = read(args, settings)
            coverage.update(found)
            for path, spans in getattr(found, "spans", {}).items():
                coverage.spans.setdefault(path, []).extend(spans)
            sources_read.append("%s %s" % (label, source))
    if not sources_read:
        raise GateError("no coverage reader is configured — give the gate an \"xccov\", \"llvm_cov\", \"istanbul\", "
                        "\"lcov\" or \"cobertura\" key")
    return coverage, sources_read


def scope_to_changes(args, settings):
    """`--changed`: `--only` the files changed against the base, repo-relative."""
    if not args.changed:
        return
    try:
        args.only = sorted(changed.changed_lines(settings.root, changed.base_ref(settings.root, args.base)))
    except changed.ChangedError as problem:
        raise GateError(str(problem))


def estimate(args, settings):
    """`--estimate`: the preflight's look ahead at the postflight. CRAP from the last coverage
    run on disk, over the functions (under `--only`, the changed files' functions) that run
    holds a record for, against the baseline; a function that would fail is a WARN, and the
    exit is 0 whatever it finds. No coverage run on disk, or none this gate can read, is
    silence: the postflight decides. So debt a branch adds is named at merge, not at release.
    With `--fail` it is a verdict instead: FAIL and exit 1, and an unreadable run exit 2."""
    inputs = estimate_inputs(args, settings)
    if inputs is None:
        return 0
    (coverage, sources_read), (complexities, ends) = inputs
    threshold = float(settings.value(args.threshold, "threshold"))
    repo = os.path.abspath(args.repo) if args.repo else settings.root
    over = [ratchet.Finding(f, line, t, {"cc": cc, "coverage": round(cov, 2), "crap": round(score, 1)})
            for f, line, t, cc, cov, score in judge(complexities, coverage, threshold, repo, ends, recorded_only=True)]
    entries, _ = ratchet.read(settings.path(args.baseline, "baseline"))
    verdict = ratchet.judge(*ratchet.restrict(over, entries, args.only), ["crap"], renames=True)
    if verdict.failed:
        print_estimate(verdict, threshold, sources_read, args.fail)
        return 1 if args.fail else 0
    if args.fail and not args.quiet:
        print_estimate_ok(args.only, threshold, sources_read)
    return 0


def estimate_inputs(args, settings):
    """(the coverage and what was read, the complexities and ends) for an estimate; None —
    silence — when there is no run to read, which under `--fail` is the error instead."""
    try:
        return gather_coverage(args, settings), complexities_for(args, settings)
    except (GateError, KeyError, coverage_reports.CoverageError):
        if args.fail:
            raise
        return None


def print_estimate_ok(only, threshold, sources_read):
    print("OK: no function %s would fail CRAP %g — estimated from %s"
          % ("in the %d changed file(s)" % len(only) if only is not None else "this run records",
             threshold, " and ".join(_aged(s) for s in sources_read)))


def print_estimate(verdict, threshold, sources_read, fail=False):
    rows = verdict.new + [finding for finding, _ in verdict.worsened]
    print("%s: %d function(s) would fail CRAP %g at the postflight — estimated from %s:"
          % ("FAIL" if fail else "WARN", len(rows), threshold, " and ".join(_aged(s) for s in sources_read)))
    for f in sorted(rows, key=lambda f: (f.file, f.line)):
        v = f.values
        print("  %s:%d  crap %.0f (cc %d, coverage %.0f%%)  %s" % (f.file, f.line, v["crap"], v["cc"], v["coverage"] * 100, f.text))
    print("Cover the untested paths or split the function now; the postflight reads a fresh coverage run and decides.")


def _aged(source):
    """"istanbul export PATH" with how old the file is, when it is one."""
    path = source.split(" ", 2)[-1]
    return "%s (%s old)" % (source, _age(path)) if os.path.exists(path) else source


def _age(path):
    hours = (time.time() - os.path.getmtime(path)) / 3600
    return "%.0fh" % hours if hours < 48 else "%.0fd" % (hours / 24)


def gate(args, settings):
    complexities, ends = complexities_for(args, settings)
    coverage, sources_read = gather_coverage(args, settings)
    threshold = float(settings.value(args.threshold, "threshold"))
    repo = os.path.abspath(args.repo) if args.repo else settings.root
    refuse_stale(settings, complexities, repo)
    baseline_path = settings.path(args.baseline, "baseline")

    over = [ratchet.Finding(f, line, t, {"cc": cc, "coverage": round(cov, 2), "crap": round(score, 1)})
            for f, line, t, cc, cov, score in judge(complexities, coverage, threshold, repo=repo, ends=ends)]
    section = {k: v for k, v in settings.section.items() if k != "baseline"} if settings.has() else {}
    measured = ratchet.provenance(complexity_tool(settings), None, section)
    if args.write_baseline:
        ratchet.write(baseline_path, over, measured)
        print("baseline written: %d function(s) over CRAP %g" % (len(over), threshold))
        return 0
    entries, stored = ratchet.read(baseline_path)
    untouched = ratchet.outside(entries, args.only)
    verdict = ratchet.judge(*ratchet.restrict(over, entries, args.only), ["crap"], stored, measured, renames=True)
    gate = ratchet.Gate(
        noun="production function(s)",
        over="over CRAP %g — complexity the tests do not pay for" % threshold,
        fix="Cover the untested paths or split the function so each piece is under the gate. Accepting new "
            "debt into the baseline is a policy decision for a person, not a fix — see quality/README.md.",
        remedy="quality/bin/check-crap.py --tighten" + (" --gate %s" % settings.gate if settings.gate else ""),
        show=lambda v: "crap %.0f (cc %d, coverage %.0f%%)" % (v["crap"], v["cc"], v["coverage"] * 100),
        brief=lambda v: "crap %s" % v["crap"])
    ok_line = ("OK: %d functions judged, %d over CRAP %g, all %d in the baseline — read from %s"
               % (len(complexities), len(over), threshold, len(entries), " and ".join(sources_read)))
    return ratchet.report(verdict, gate, len(entries), ok_line, quiet=args.quiet, strict=args.strict,
                          context=["read from %s" % " and ".join(sources_read)],
                          tighten=args.tighten, baseline=(baseline_path, measured, untouched))


def complexity_tool(settings):
    """The name of what measured complexity, for the baseline's provenance."""
    if settings.has("complexity", "tool"):
        return str(settings.value(None, "complexity", "tool"))
    return "swiftlint"



if __name__ == "__main__":
    sys.exit(main())

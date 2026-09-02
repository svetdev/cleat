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
                 "exports": "…/.build/*/debug/codecov/AcmeCore.json"}                    # newest wins
  }

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

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
import ratchet
from extractors import complexity as complexity_readers
from extractors import coverage as coverage_reports

SECTION = "crap"


class Settings:
    """The project facts: each from its flag when given, else from the `crap` section of
    quality.json — opened the first time a value has to come from it."""

    def __init__(self, explicit_config):
        self.explicit = explicit_config
        self._config = None

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

def coverage_at(coverage, path, line):
    """The coverage recorded for the declaration at `line` — on its own line, or on an
    attribute line above it or the line its multi-line signature opens the body on
    (SwiftLint reports the `func` line; xccov records the function elsewhere); 0.0 when
    nothing recorded it."""
    cov = coverage.get((path, line))
    if cov is not None:
        return cov
    for candidate in coverage_reports.nearby_declaration_lines(path, line):
        if (path, candidate) in coverage:
            return coverage[(path, candidate)]
    return 0.0


def judge(complexities, coverage, threshold, repo):
    """[(file relative to `repo`, line, text, cc, cov, crap)] for every function over the gate."""
    repo = os.path.realpath(repo)  # the files are realpaths; a symlinked repo must not relativise to ../../
    over = []
    for (path, line), cc in complexities.items():
        cov = coverage_at(coverage, path, line)
        score = crap(cc, cov)
        if score > threshold:
            over.append((os.path.relpath(path, repo), line, complexity_readers.declaration_text(path, line), cc, cov, score))
    over.sort(key=lambda o: (-o[5], o[0], o[1]))
    return over


class GateError(Exception):
    """A reason the gate cannot judge today; printed as FAIL, exit 2."""


def complexities_for(args, settings):
    """From --lint (SwiftLint json) or --lizard-csv (a saved lizard run); else the reader
    `crap.complexity.tool` names over its sources — lizard for Rust/TypeScript — or, with
    no such key, SwiftLint over `crap.sources`."""
    if args.lint:
        with open(args.lint) as handle:
            return complexity_readers.complexities_from_swiftlint(json.load(handle))
    if args.lizard_csv:
        with open(args.lizard_csv) as handle:
            functions, _ = complexity_readers.functions_from_csv(handle.read())
        # a saved run may cover every stack; a gate judges only its own sources
        if settings.has("complexity", "sources"):
            roots = [os.path.join(os.path.realpath(r), "") for r in settings.config.paths(settings.value(None, "complexity", "sources"))]
            functions = [f for f in functions if any(f.path.startswith(r) for r in roots)]
        return complexity_readers.complexities(functions)
    try:
        if settings.has("complexity", "tool") and settings.value(None, "complexity", "tool") == "lizard":
            spec = settings.value(None, "complexity")
            return complexity_readers.lizard_complexities(settings.config.paths(spec["sources"]), spec["languages"],
                                                          spec.get("exclude", []), spec.get("skip_rust_tests", True))
        return complexity_readers.swiftlint_complexities(settings.config.paths(settings.value(None, "sources")))
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
        bundle = args.bundle or coverage_reports.newest(settings.config.path(settings.value(None, "xccov", "bundles")))
        if bundle is None:
            raise GateError("no .xcresult bundle matches %s — run the suite first" % settings.value(None, "xccov", "bundles"))
        report, source = coverage_reports.read_xccov_bundle(bundle), bundle
    path_map = path_map_for(settings, "xccov")
    note_package_files(coverage_reports.xccov_package_files(report, root, path_map), args, settings)
    return coverage_reports.from_xccov(report, root, path_map), source


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


def package_coverage(args, settings):
    """The llvm-cov root's functions, and the file they were read from: from --codecov,
    else the newest export."""
    root = settings.path(args.package_sources, "llvm_cov", "sources")
    if args.codecov:
        with open(args.codecov) as handle:
            return coverage_reports.from_codecov(json.load(handle), root, path_map_for(settings, "llvm_cov")), args.codecov
    pattern = settings.value(None, "llvm_cov", "exports")
    export = coverage_reports.newest(settings.config.path(pattern))
    if export is None:
        raise GateError("no llvm-cov export matches %s — run the package's tests with --enable-code-coverage" % pattern)
    with open(export) as handle:
        return coverage_reports.from_codecov(json.load(handle), root, path_map_for(settings, "llvm_cov")), export


def web_coverage(args, settings):
    """The istanbul root's functions and the file they were read from: --istanbul, else
    the newest `crap.istanbul.exports` match."""
    root = settings.path(args.web_sources, "istanbul", "sources")
    if args.istanbul:
        with open(args.istanbul) as handle:
            return coverage_reports.from_istanbul(json.load(handle), root, path_map_for(settings, "istanbul")), args.istanbul
    pattern = settings.value(None, "istanbul", "exports")
    export = coverage_reports.newest(settings.config.path(pattern))
    if export is None:
        raise GateError("no istanbul export matches %s — run the web tests with --coverage first" % pattern)
    with open(export) as handle:
        return coverage_reports.from_istanbul(json.load(handle), root, path_map_for(settings, "istanbul")), export


def report_coverage(args, settings, key, flag):
    """The LCOV or Cobertura root's functions and the file they were read from: the
    flag's file, else the newest `crap.<key>.exports` match; the root from
    --report-sources, else `crap.<key>.sources`."""
    root = settings.path(args.report_sources, key, "sources")
    if flag:
        path = flag
    else:
        pattern = settings.value(None, key, "exports")
        path = coverage_reports.newest(settings.config.path(pattern))
        if path is None:
            raise GateError("no %s report matches %s — run the tests with coverage first" % (key, pattern))
    base_dir = os.path.abspath(args.repo) if args.repo else (settings.root if settings.has() else os.path.dirname(os.path.abspath(path)))
    report = coverage_reports.read(path, base_dir, path_map_for(settings, key))
    return coverage_reports.function_coverage(report, root), path


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
    ratchet.add_strict_argument(parser)
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    settings = Settings(args.config)
    settings.gate = args.gate
    try:
        return gate(args, settings)
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


def gate(args, settings):
    complexities = complexities_for(args, settings)
    coverage, sources_read = gather_coverage(args, settings)
    threshold = float(settings.value(args.threshold, "threshold"))
    repo = os.path.abspath(args.repo) if args.repo else settings.root
    baseline_path = settings.path(args.baseline, "baseline")

    over = [ratchet.Finding(f, line, t, {"cc": cc, "coverage": round(cov, 2), "crap": round(score, 1)})
            for f, line, t, cc, cov, score in judge(complexities, coverage, threshold, repo=repo)]
    section = {k: v for k, v in settings.section.items() if k != "baseline"} if settings.has() else {}
    measured = ratchet.provenance(complexity_tool(settings), None, section)
    if args.write_baseline:
        ratchet.write(baseline_path, over, measured)
        print("baseline written: %d function(s) over CRAP %g" % (len(over), threshold))
        return 0
    entries, stored = ratchet.read(baseline_path)
    verdict = ratchet.judge(over, entries, ["crap"], stored, measured)
    gate = ratchet.Gate(
        noun="production function(s)",
        over="over CRAP %g — complexity the tests do not pay for" % threshold,
        fix="Cover the untested paths or split the function so each piece is under the gate. Accepting new "
            "debt into the baseline is a policy decision for a person, not a fix — see quality/README.md.",
        remedy="quality/bin/check-crap.py --write-baseline" + (" --gate %s" % settings.gate if settings.gate else ""),
        show=lambda v: "crap %.0f (cc %d, coverage %.0f%%)" % (v["crap"], v["cc"], v["coverage"] * 100),
        brief=lambda v: "crap %s" % v["crap"])
    ok_line = ("OK: %d functions judged, %d over CRAP %g, all %d in the baseline — read from %s"
               % (len(complexities), len(over), threshold, len(entries), " and ".join(sources_read)))
    return ratchet.report(verdict, gate, len(entries), ok_line, quiet=args.quiet, strict=args.strict,
                          context=["read from %s" % " and ".join(sources_read)])


def complexity_tool(settings):
    """The name of what measured complexity, for the baseline's provenance."""
    if settings.has("complexity", "tool"):
        return str(settings.value(None, "complexity", "tool"))
    return "swiftlint"



if __name__ == "__main__":
    sys.exit(main())

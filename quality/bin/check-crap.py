#!/usr/bin/env python3
"""check-crap — fail on a production function whose complexity is not paid for by tests.

CRAP (Change Risk Anti-Patterns) is the score Uncle Bob runs over agent-written
code: for a function with cyclomatic complexity `cc` and test coverage `cov`,

    crap = cc² × (1 − cov)³ + cc

A fully covered function scores its complexity; an uncovered one scores about
its complexity squared. So a simple uncovered function passes, a complex
covered one passes, and a complex uncovered one does not — which is the one
an agent writes when it is in a hurry, and the one nobody wants to touch
later. The complexity gate (check-complexity.sh) catches complexity alone;
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
`lizard_reader.py` uses for `LIZARD_TIMEOUT_SECONDS`. A child that runs past
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

  "crap": {
    "threshold": 8,                                  # the gate
    "baseline":  "Kiteloop/.crap-baseline.json",     # the ratchet
    "sources":   ["Kiteloop/Kiteloop", "…/Sources/KiteloopCore"],   # SwiftLint runs over these
    "xccov":    {"sources": "Kiteloop/Kiteloop",     # the root whose functions the xccov reader keeps
                 "bundles": "~/Library/Developer/Xcode/DerivedData/*/Logs/Test/*.xcresult"},  # newest wins
    "llvm_cov": {"sources": "…/Sources/KiteloopCore",   # the root the llvm-cov reader keeps
                 "exports": "…/.build/*/debug/codecov/KiteloopCore.json"}                    # newest wins
  }

A flag overrides its key — `--threshold`, `--baseline`, `--app-sources` (the
xccov root), `--package-sources` (the llvm-cov root), `--repo` (what paths
are reported relative to) — and the config is only opened for a value no flag
supplied, so a fully flagged run needs no quality.json at all. A key the
config lacks fails naming the key.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lizard_reader  # noqa: E402
import quality_config  # noqa: E402

SECTION = "crap"
SWIFTLINT_TIMEOUT_SECONDS = int(os.environ.get("SWIFTLINT_TIMEOUT_SECONDS", "600"))
XCCOV_TIMEOUT_SECONDS = int(os.environ.get("XCCOV_TIMEOUT_SECONDS", "600"))


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


# ---------------------------------------------------------------- complexity

LINT_CONFIG = """only_rules: [cyclomatic_complexity]
cyclomatic_complexity: {warning: 1, error: 1, ignores_case_statements: true}
excluded: [KiteloopTests, KiteloopUITests, Frameworks, Tests]
"""


def lint_complexities(roots):
    """{(abs file, line): cc} from SwiftLint over `roots`, threshold 1."""
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as handle:
        handle.write(LINT_CONFIG)
        config = handle.name
    try:
        try:
            proc = subprocess.run(["swiftlint", "lint", "--quiet", "--reporter", "json", "--config", config, *roots],
                                  capture_output=True, text=True, timeout=SWIFTLINT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise GateError("swiftlint ran past its %ds time limit — ended" % SWIFTLINT_TIMEOUT_SECONDS)
        raw = proc.stdout.strip() or "[]"
        return complexities_from_lint(json.loads(raw))
    finally:
        os.unlink(config)


def complexities_from_lint(violations):
    out = {}
    for v in violations:
        m = re.search(r"currently complexity is (\d+)", v.get("reason", ""))
        if m and v.get("file"):
            out[(os.path.realpath(v["file"]), int(v["line"]))] = int(m.group(1))
    return out


def lizard_complexities(roots, languages, excludes, skip_rust_tests):
    """{(abs file, line): cc} from lizard over `roots` — Rust, TypeScript and the
    other stacks SwiftLint does not read (see lizard_reader.py)."""
    text = lizard_reader.run_lizard(roots, languages, excludes)
    functions, _ = lizard_reader.functions_from_csv(text, skip_rust_tests=skip_rust_tests)
    return lizard_reader.complexities(functions)


# ---------------------------------------------------------------- coverage

def remap(path, path_map):
    """`path` with the first matching `path_map` prefix replaced — coverage written
    inside a container names `/work/apps/…` for what the host calls `<repo>/apps/…`."""
    for source, target in (path_map or {}).items():
        if path.startswith(source):
            return target + path[len(source):]
    return path


def coverage_from_istanbul(report, sources_root):
    """{(abs file, line): coverage} from an istanbul `coverage-final.json` (vitest
    --coverage, c8/v8 or istanbul providers alike): a function's coverage is the share
    of the statements inside its range that ran; its line is its declaration's."""
    root = os.path.join(os.path.realpath(sources_root), "")
    out = {}
    for entry in report.values():
        path = os.path.realpath(entry.get("path", ""))
        if not path.startswith(root):
            continue
        statements = entry.get("statementMap", {})
        hits = entry.get("s", {})
        for fn_id, fn in entry.get("fnMap", {}).items():
            loc = fn.get("loc", {})
            start, end = loc.get("start", {}).get("line"), loc.get("end", {}).get("line")
            if start is None or end is None:
                continue
            inside = [sid for sid, st in statements.items()
                      if start <= st.get("start", {}).get("line", -1) <= end]
            if inside:
                cov = sum(1 for sid in inside if hits.get(sid, 0) > 0) / len(inside)
            else:
                cov = 1.0 if entry.get("f", {}).get(fn_id, 0) > 0 else 0.0
            key = (path, int(fn.get("decl", {}).get("start", {}).get("line", start)))
            out[key] = max(out.get(key, 0.0), cov)
    return out


def coverage_from_xccov(report, sources_root):
    """{(abs file, line): coverage} from `xccov view --report --json`."""
    out = {}
    for target in report.get("targets", []):
        for f in target.get("files", []):
            path = os.path.realpath(f.get("path", ""))
            # under the root directory — not a string prefix of it: `…/Kiteloop/Kiteloop` must
            # not admit `…/Kiteloop/KiteloopCore/…`, whose records in the app bundle are all 0%
            if not path.startswith(os.path.join(os.path.realpath(sources_root), "")):
                continue
            for fn in f.get("functions", []):
                lines = fn.get("executableLines", 0)
                cov = (fn.get("coveredLines", 0) / lines) if lines else 1.0
                out[(path, int(fn.get("lineNumber", 0)))] = cov
    return out


def coverage_from_codecov(export, sources_root, path_map=None):
    """{(abs file, line): coverage} from llvm-cov's export (swift test --enable-code-coverage):
    a function's coverage is the share of its regions that ran; its line is its first region's."""
    # A function's body is one record; each default argument is another, named by the
    # body's mangled name plus `fA<n>_` (a closure literal as the default adds its own
    # suffix to that) and keyed at the argument's line — before the body's brace. A
    # test that passes every argument never runs the thunk, so a join walking down
    # from the declaration met a 0% record first. Those records are not functions;
    # nested functions (`…L_`) and closures (`…fU_`) are left to their own lines.
    root = os.path.join(os.path.realpath(sources_root), "")  # under the directory, not a string prefix
    records = []
    for data in export.get("data", []):
        for fn in data.get("functions", []):
            files = [os.path.realpath(remap(p, path_map)) for p in fn.get("filenames", [])]
            files = [p for p in files if p.startswith(root)]
            regions = fn.get("regions", [])
            if files and regions:
                records.append((files[0], fn.get("name", ""), regions))
    names = {(path, name) for path, name, _ in records}
    out = {}
    for path, name, regions in records:
        if any((path, name[:m.start()]) in names for m in re.finditer(r"fA\d*_", name)):
            continue
        # regions: [lineStart, colStart, lineEnd, colEnd, count, fileID, expandedFileID, kind]
        ran = sum(1 for r in regions if r[4] > 0)
        key = (path, min(r[0] for r in regions))
        # two records on one line (specialisations): keep the best
        out[key] = max(out.get(key, 0.0), ran / len(regions))
    return out


# ---------------------------------------------------------------- the judgement

def attribute_lines_above(path, line):
    """The lines directly above `line` that are attributes, nearest first."""
    try:
        with open(path, errors="replace") as handle:
            lines = handle.read().split("\n")
    except OSError:
        return []
    out = []
    current = line - 1
    while 0 < current <= len(lines) and lines[current - 1].strip().startswith("@"):
        out.append(current)
        current -= 1
    return out


def signature_lines_below(path, line):
    """The lines after `line` up to and including the one the body opens on —
    the rest of a multi-line signature — nearest first; nothing past a brace."""
    try:
        with open(path, errors="replace") as handle:
            lines = handle.read().split("\n")
    except OSError:
        return []
    # The body opens on the first `{` after the parameter list's parentheses balance —
    # a default argument may itself be a closure, `f: () -> Int = { 1 }`, and its
    # brace is inside the signature, not the end of it.
    depth = 0
    opened = False
    def opens_body(text):
        nonlocal depth, opened
        for ch in text:
            if ch == "(":
                depth += 1; opened = True
            elif ch == ")":
                depth -= 1
            elif ch == "{" and (not opened or depth == 0):
                return True
        return False
    if 0 < line <= len(lines) and opens_body(lines[line - 1]):
        return []
    out = []
    current = line + 1
    while current <= len(lines) and current <= line + 40:
        out.append(current)
        if opens_body(lines[current - 1]):
            break
        current += 1
    return out


def declaration_text(path, line):
    try:
        with open(path, errors="replace") as handle:
            lines = handle.read().split("\n")
        return lines[line - 1].strip() if 0 < line <= len(lines) else ""
    except OSError:
        return ""


def judge(complexities, coverage, threshold, repo):
    """[(file relative to `repo`, line, text, cc, cov, crap)] for every function over the gate."""
    repo = os.path.realpath(repo)  # the files are realpaths; a symlinked repo must not relativise to ../../
    over = []
    for (path, line), cc in complexities.items():
        cov = coverage.get((path, line))
        if cov is None:
            # SwiftLint reports the `func` line; xccov records the function on
            # its first attribute line (`@MainActor`) or, for a signature that
            # spans lines, on the line its body opens. Walk up through attribute
            # lines and down through the signature to the opening brace — never
            # past either, into the function above or below.
            for candidate in attribute_lines_above(path, line) + signature_lines_below(path, line):
                if (path, candidate) in coverage:
                    cov = coverage[(path, candidate)]
                    break
        if cov is None:
            cov = 0.0
        score = crap(cc, cov)
        if score > threshold:
            over.append((os.path.relpath(path, repo), line, declaration_text(path, line), cc, cov, score))
    over.sort(key=lambda o: (-o[5], o[0], o[1]))
    return over


def baseline_keys(entries):
    return {(e["file"], e["text"]) for e in entries}


def stale_baseline_entries(baseline, over):
    """Baseline entries whose (file, text) key matched nothing in `over` this run —
    the function was covered, split, renamed or deleted, and the entry is still accepted."""
    over_keys = {(o[0], o[2]) for o in over}
    return [e for e in baseline if (e["file"], e["text"]) not in over_keys]


def print_stale_note(stale):
    if not stale:
        return
    print("NOTE: %d baseline entr%s matched nothing this run — already fixed, covered, split or renamed:"
          % (len(stale), "y" if len(stale) == 1 else "ies"))
    for e in stale[:20]:
        print("  %s  crap %s  %s" % (e["file"], e["crap"], e["text"][:70]))
    if len(stale) > 20:
        print("  … and %d more" % (len(stale) - 20))
    print("Drop what's fixed, re-accept what's still over the gate: quality/bin/check-crap.py --write-baseline")


# ---------------------------------------------------------------- the inputs

class GateError(Exception):
    """A reason the gate cannot judge today; printed as FAIL, exit 2."""


def newest(pattern):
    """The newest file matching the glob `pattern` (`~` expands), or None."""
    candidates = glob.glob(os.path.expanduser(pattern))
    return max(candidates, key=os.path.getmtime) if candidates else None


def complexities_for(args, settings):
    """From --lint (SwiftLint json) or --lizard-csv (a saved lizard run); else the reader
    `crap.complexity.tool` names over its sources — lizard for Rust/TypeScript — or, with
    no such key, SwiftLint over `crap.sources`."""
    if args.lint:
        with open(args.lint) as handle:
            return complexities_from_lint(json.load(handle))
    if args.lizard_csv:
        with open(args.lizard_csv) as handle:
            functions, _ = lizard_reader.functions_from_csv(handle.read())
        # a saved run may cover every stack; a gate judges only its own sources
        if settings.has("complexity", "sources"):
            roots = [os.path.join(os.path.realpath(r), "") for r in settings.config.paths(settings.value(None, "complexity", "sources"))]
            functions = [f for f in functions if any(f.path.startswith(r) for r in roots)]
        return lizard_reader.complexities(functions)
    if settings.has("complexity", "tool") and settings.value(None, "complexity", "tool") == "lizard":
        spec = settings.value(None, "complexity")
        try:
            return lizard_complexities(settings.config.paths(spec["sources"]), spec["languages"],
                                       spec.get("exclude", []), spec.get("skip_rust_tests", True))
        except lizard_reader.LizardError as problem:
            raise GateError(str(problem))
    if not shutil_which("swiftlint"):
        raise GateError("swiftlint is not installed — brew install swiftlint")
    return lint_complexities(settings.config.paths(settings.value(None, "sources")))


def read_xccov_bundle(bundle):
    """`xcrun xccov view --report --json` for `bundle`, within XCCOV_TIMEOUT_SECONDS, or a GateError."""
    try:
        proc = subprocess.run(["xcrun", "xccov", "view", "--report", "--json", bundle],
                               capture_output=True, text=True, timeout=XCCOV_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise GateError("xccov ran past its %ds time limit — ended" % XCCOV_TIMEOUT_SECONDS)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise GateError("%s carries no coverage — the suite must run with -enableCodeCoverage YES" % bundle)
    return json.loads(proc.stdout)


def app_coverage(args, settings):
    """The xccov root's functions, and the file they were read from: from --xccov, a
    given --bundle, or else the newest bundle matching crap.xccov.bundles, through xcrun.
    A --bundle skips the glob entirely — it names the run, so nothing is guessed."""
    root = settings.path(args.app_sources, "xccov", "sources")
    if args.xccov:
        with open(args.xccov) as handle:
            return coverage_from_xccov(json.load(handle), root), args.xccov
    if args.bundle:
        bundle = args.bundle
    else:
        pattern = settings.value(None, "xccov", "bundles")
        bundle = newest(settings.config.path(pattern))
        if bundle is None:
            raise GateError("no .xcresult bundle matches %s — run the suite first" % pattern)
    return coverage_from_xccov(read_xccov_bundle(bundle), root), bundle


def llvm_path_map(args, settings):
    """`llvm_cov.path_map`: {prefix in the export: prefix on this machine}, values resolved
    against quality.json's directory — the export was written where the tests ran."""
    if args.codecov or not settings.has("llvm_cov", "path_map"):
        return {}
    raw = settings.value(None, "llvm_cov", "path_map")
    return {source: os.path.join(settings.config.path(target), "") for source, target in raw.items()}


def package_coverage(args, settings):
    """The llvm-cov root's functions, and the file they were read from: from --codecov,
    else the newest export."""
    root = settings.path(args.package_sources, "llvm_cov", "sources")
    if args.codecov:
        with open(args.codecov) as handle:
            return coverage_from_codecov(json.load(handle), root), args.codecov
    pattern = settings.value(None, "llvm_cov", "exports")
    export = newest(settings.config.path(pattern))
    if export is None:
        raise GateError("no llvm-cov export matches %s — run the package's tests with --enable-code-coverage" % pattern)
    with open(export) as handle:
        return coverage_from_codecov(json.load(handle), root, llvm_path_map(args, settings)), export


def web_coverage(args, settings):
    """The istanbul root's functions and the file they were read from: --istanbul, else
    the newest `crap.istanbul.exports` match."""
    root = settings.path(args.web_sources, "istanbul", "sources")
    if args.istanbul:
        with open(args.istanbul) as handle:
            return coverage_from_istanbul(json.load(handle), root), args.istanbul
    pattern = settings.value(None, "istanbul", "exports")
    export = newest(settings.config.path(pattern))
    if export is None:
        raise GateError("no istanbul export matches %s — run the web tests with --coverage first" % pattern)
    with open(export) as handle:
        return coverage_from_istanbul(json.load(handle), root), export


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
    parser.add_argument("--gate", help="which gate to run when crap is a list of gates (default: the single section)")
    parser.add_argument("--baseline", help="the ratchet file (default: crap.baseline)")
    parser.add_argument("--app-sources", help="the root the xccov reader keeps (default: crap.xccov.sources)")
    parser.add_argument("--package-sources", help="the root the llvm-cov reader keeps (default: crap.llvm_cov.sources)")
    parser.add_argument("--repo", help="paths are reported relative to this (default: the directory of quality.json)")
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    settings = Settings(args.config)
    settings.gate = args.gate
    try:
        return gate(args, settings)
    except (GateError, KeyError) as problem:
        print("FAIL: %s" % (problem.args[0] if problem.args else problem), file=sys.stderr)
        return 2


def gate(args, settings):
    complexities = complexities_for(args, settings)
    coverage, sources_read = {}, []
    if args.xccov or args.bundle or settings.has("xccov"):
        app_cov, app_source = app_coverage(args, settings)
        coverage.update(app_cov); sources_read.append("bundle %s" % app_source)
    if args.codecov or settings.has("llvm_cov"):
        package_cov, package_source = package_coverage(args, settings)
        coverage.update(package_cov); sources_read.append("package export %s" % package_source)
    if args.istanbul or settings.has("istanbul"):
        web_cov, web_source = web_coverage(args, settings)
        coverage.update(web_cov); sources_read.append("istanbul export %s" % web_source)
    if not sources_read:
        raise GateError("no coverage reader is configured — give the gate an \"xccov\", \"llvm_cov\" or \"istanbul\" key")
    threshold = float(settings.value(args.threshold, "threshold"))
    repo = os.path.abspath(args.repo) if args.repo else settings.root
    baseline_path = settings.path(args.baseline, "baseline")

    over = judge(complexities, coverage, threshold, repo=repo)
    if args.write_baseline:
        with open(baseline_path, "w") as handle:
            json.dump([{"file": f, "text": t, "cc": cc, "coverage": round(cov, 2), "crap": round(score, 1)}
                       for f, _, t, cc, cov, score in over], handle, indent=1)
            handle.write("\n")
        print("baseline written: %d function(s) over CRAP %g" % (len(over), threshold))
        return 0
    baseline = []
    if os.path.isfile(baseline_path):
        with open(baseline_path) as handle:
            baseline = json.load(handle)
    known = baseline_keys(baseline)
    stale = stale_baseline_entries(baseline, over)
    new = [o for o in over if (o[0], o[2]) not in known]
    if new:
        print("FAIL: %d production function(s) over CRAP %g — complexity the tests do not pay for — beyond the %d in the baseline:"
              % (len(new), threshold, len(baseline)))
        print("  read from %s" % " and ".join(sources_read))
        for f, line, text, cc, cov, score in new:
            print("  %s:%d  crap %.0f (cc %d, coverage %.0f%%)  %s" % (f, line, score, cc, cov * 100, text[:70]))
        print("Cover the paths, split the function, or — on purpose, with the reason in the commit — accept it: quality/bin/check-crap.py --write-baseline")
        print_stale_note(stale)
        return 1
    if not args.quiet:
        print("OK: %d functions judged, %d over CRAP %g, all %d in the baseline — read from %s"
              % (len(complexities), len(over), threshold, len(baseline), " and ".join(sources_read)))
    print_stale_note(stale)
    return 0


def shutil_which(name):
    from shutil import which
    return which(name)


if __name__ == "__main__":
    sys.exit(main())

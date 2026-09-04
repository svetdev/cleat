"""complexity — per-function cyclomatic complexity and length, from lizard or SwiftLint.

Two readers, one shape: `Function(path, line, end, cc, length, name)`, keyed by
`(realpath, start line)` so a coverage reader can join on position.

lizard (pip/brew `lizard`) parses Python, TypeScript/TSX, JavaScript, Rust,
Swift, Go, Kotlin, Java, Ruby and more, and prints one CSV row per function:

    NLOC, CCN, tokens, params, length, "name@start-end@file", file, name, long name, start, end

Inline Rust tests are the one wrinkle: `#[cfg(test)] mod tests { … }` lives in
the production file, and a test's complexity is not production complexity.
`rust_test_ranges(path)` finds each `#[cfg(test)]` item's lines; a function
starting inside one is dropped when `skip_rust_tests` is on, and a function
after the module's closing brace is production.

SwiftLint reads Swift natively: `cyclomatic_complexity` and
`function_body_length` at threshold 1 report every function's numbers, and
the two reports are merged by file and line. Each runner gives its child a
wall-clock ceiling (`LIZARD_TIMEOUT_SECONDS`, `SWIFTLINT_TIMEOUT_SECONDS`,
600s by default) and turns a hang into a `ToolError` naming it.

`measure(spec, root)` is the one entrypoint the gates use: `spec` is the
`complexity` section — `tool` (lizard or swiftlint; lizard when `languages`
is given), `sources`, `languages`, `exclude`, `exclude_except`,
`skip_rust_tests` — and it returns (functions, skipped as tests, tool, version).
"""

import csv
import io
import json
import os
import re
import shutil
import subprocess
import tempfile

from . import patterns

LIZARD_TIMEOUT_SECONDS = int(os.environ.get("LIZARD_TIMEOUT_SECONDS", "600"))


class ToolError(Exception):
    """A tool is missing or refused to run; printed as FAIL, exit 2."""


# `r"` or `r#"`, not the tail of an identifier or of a string such as "owner".
RAW_STRING_RE = re.compile(r'(?<![\w"])b?r(#*)"(.*?)"\1', re.S)


def masked_raw_strings(text):
    """`text` with every Rust raw string's body blanked, newlines kept, so a line
    number in the copy is a line number in the original. lizard reads a multi-line
    `r#"…"#` as code from its second line on and counts every `?` in it — one per
    sqlx `"column?"` nullability override — as a branch."""
    def blank(match):
        opener = match.group(0)[:match.start(2) - match.start(0)]
        return '%s%s"%s' % (opener, re.sub(r"[^\n]", " ", match.group(2)), match.group(1))
    return RAW_STRING_RE.sub(blank, text)


def _mirror_rust(paths, mirror):
    """Every .rs file under `paths`, masked, at its own absolute path under `mirror`;
    returns the mirror-side paths to hand lizard, one per entry of `paths`."""
    mirrored = []
    for path in paths:
        real = os.path.realpath(path)
        files = [real] if os.path.isfile(real) else [os.path.join(d, f) for d, _, fs in os.walk(real) for f in fs]
        os.makedirs(mirror + (os.path.dirname(real) if os.path.isfile(real) else real), exist_ok=True)
        for file in files:
            if not file.endswith(".rs"):
                continue
            target = mirror + file
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(file, errors="replace") as source, open(target, "w") as copy:
                copy.write(masked_raw_strings(source.read()))
        mirrored.append(mirror + real)
    return mirrored


def _lizard_csv(paths, languages, excludes):
    """One lizard --csv run per reader over `paths`, or a ToolError: the other
    languages over the tree as it is, Rust over a masked mirror of it."""
    others = [language for language in languages if language != "rust"]
    output = _lizard(paths, others, excludes) if others else ""
    if "rust" in languages:
        with tempfile.TemporaryDirectory(prefix="lizard-rust-") as tmp:
            mirror = os.path.realpath(tmp)
            output += _lizard(_mirror_rust(paths, mirror), ["rust"], excludes).replace(mirror, "")
    return output


def _lizard(paths, languages, excludes):
    """One lizard --csv run over `paths`, or a ToolError."""
    command = ["lizard", "--csv"]
    for language in languages:
        command += ["-l", language]
    for pattern in excludes:
        command += ["-x", pattern]
    command += list(paths)
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=LIZARD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise ToolError("lizard ran past its %ds time limit — ended" % LIZARD_TIMEOUT_SECONDS)
    if proc.returncode not in (0, 1):  # 1 is lizard's own "over its thresholds" — not ours to act on
        raise ToolError("lizard exited %d: %s" % (proc.returncode, proc.stderr.strip()[:300]))
    return proc.stdout


def run_lizard(sources, languages, excludes, exclude_except=None):
    """lizard's CSV over `sources` with `excludes` applied, plus — for `exclude_except`,
    paths an exclude glob would otherwise drop by name but that are production code —
    a second pass over exactly those paths with no exclude at all. Raises ToolError."""
    if not shutil.which("lizard"):
        raise ToolError("lizard is not installed — brew install lizard (or pip install lizard)")
    output = _lizard_csv(sources, languages, excludes)
    if exclude_except:
        output += _lizard_csv(exclude_except, languages, [])
    return output


class Function:
    __slots__ = ("path", "line", "end", "cc", "length", "name")

    def __init__(self, path, line, end, cc, length, name):
        self.path, self.line, self.end, self.cc, self.length, self.name = path, line, end, cc, length, name


def functions_from_csv(text, skip_rust_tests=True):
    """[Function] from lizard CSV; inline Rust test modules dropped when asked.
    Returns (functions, skipped_as_tests)."""
    functions, skipped = [], 0
    test_ranges = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 11:
            continue
        try:
            cc, length, start, end = int(row[1]), int(row[4]), int(row[9]), int(row[10])
        except ValueError:
            continue  # a header line, if lizard ever prints one
        path = os.path.realpath(row[6])
        if skip_rust_tests and path.endswith(".rs"):
            if path not in test_ranges:
                test_ranges[path] = patterns.rust_test_ranges(path)
            if patterns.in_ranges(test_ranges[path], start):
                skipped += 1
                continue
        functions.append(Function(path, start, end, cc, length, row[7]))
    return functions, skipped


def complexities(functions):
    """{(realpath, line): cc} — the shape the CRAP judge joins on."""
    return {(f.path, f.line): f.cc for f in functions}


def lizard_version():
    """lizard's own version string, for baseline provenance; None when it cannot be asked."""
    try:
        proc = subprocess.run(["lizard", "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() or None


# ---------------------------------------------------------------- swiftlint

SWIFTLINT_TIMEOUT_SECONDS = int(os.environ.get("SWIFTLINT_TIMEOUT_SECONDS", "600"))
SWIFTLINT_CONFIG = """only_rules: [cyclomatic_complexity, function_body_length]
cyclomatic_complexity: {warning: 1, error: 1, ignores_case_statements: true}
function_body_length: {warning: 1, error: 1}
excluded: ["**/*Tests", Frameworks, Tests]
"""
CC_RE = re.compile(r"currently complexity is (\d+)")
LENGTH_RE = re.compile(r"currently spans (\d+) lines")


def swiftlint_violations(roots):
    """SwiftLint's JSON violations over `roots` with the two measuring rules at
    threshold 1, or a ToolError."""
    if not shutil.which("swiftlint"):
        raise ToolError("swiftlint is not installed — brew install swiftlint")
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as handle:
        handle.write(SWIFTLINT_CONFIG)
        config = handle.name
    try:
        try:
            proc = subprocess.run(["swiftlint", "lint", "--quiet", "--reporter", "json", "--config", config, *roots],
                                  capture_output=True, text=True, timeout=SWIFTLINT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise ToolError("swiftlint ran past its %ds time limit — ended" % SWIFTLINT_TIMEOUT_SECONDS)
        return json.loads(proc.stdout.strip() or "[]")
    finally:
        os.unlink(config)


def functions_from_swiftlint(violations):
    """[Function] from SwiftLint's violations: the complexity and length reports for
    one declaration merged by file and line; a number nobody reported is 1."""
    found = {}
    for v in violations:
        if not v.get("file"):
            continue
        key = (os.path.realpath(v["file"]), int(v.get("line", 0)))
        cc = CC_RE.search(v.get("reason", ""))
        length = LENGTH_RE.search(v.get("reason", ""))
        entry = found.setdefault(key, [1, 1])
        if cc:
            entry[0] = int(cc.group(1))
        if length:
            entry[1] = int(length.group(1))
    return [Function(path, line, line, cc, length, "") for (path, line), (cc, length) in sorted(found.items())]


def complexities_from_swiftlint(violations):
    """{(abs file, line): cc} — the CRAP join, from the complexity reports alone."""
    return {(f.path, f.line): f.cc for f in functions_from_swiftlint(violations) if any(
        CC_RE.search(v.get("reason", "")) for v in violations if os.path.realpath(v.get("file", "")) == f.path and int(v.get("line", 0)) == f.line)}


def swiftlint_complexities(roots):
    return complexities_from_swiftlint(swiftlint_violations(roots))


def lizard_complexities(roots, languages, excludes, skip_rust_tests=True):
    functions, _ = functions_from_csv(run_lizard(roots, languages, excludes), skip_rust_tests=skip_rust_tests)
    return complexities(functions)


def swiftlint_version():
    try:
        proc = subprocess.run(["swiftlint", "version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() or None


def declaration_text(path, line):
    try:
        with open(path, errors="replace") as handle:
            lines = handle.read().split("\n")
        return lines[line - 1].strip() if 0 < line <= len(lines) else ""
    except OSError:
        return ""


# ---------------------------------------------------------------- the entrypoint

def tool_of(spec):
    return spec.get("tool") or ("lizard" if spec.get("languages") else "swiftlint")


def measure(spec, roots, exclude_except=()):
    """(functions, skipped as tests, tool, version) for the `complexity` section `spec`
    over the absolute `roots`. Raises ToolError."""
    if tool_of(spec) == "swiftlint":
        return functions_from_swiftlint(swiftlint_violations(roots)), 0, "swiftlint", swiftlint_version()
    text = run_lizard(roots, spec.get("languages", []), spec.get("exclude", []), list(exclude_except))
    functions, skipped = functions_from_csv(text, skip_rust_tests=spec.get("skip_rust_tests", True))
    return functions, skipped, "lizard", lizard_version()

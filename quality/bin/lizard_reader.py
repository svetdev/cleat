"""Per-function cyclomatic complexity from `lizard`, for the stacks SwiftLint does not read.

lizard (pip/brew `lizard`) parses Rust, TypeScript/TSX, JavaScript, Python and
more, and prints one CSV row per function:

    NLOC, CCN, tokens, params, length, "name@start-end@file", file, name, long name, start, end

Two readers share this: `check-complexity-lizard.py` (the ratchet over cc and
length) and `check-crap.py` (cc joined to coverage). Both key a function by
`(realpath, start line)` so the join with a coverage reader is by position, the
way the SwiftLint readers already work.

Inline Rust tests are the one wrinkle: `#[cfg(test)] mod tests { … }` lives in
the production file, and a test's complexity is not production complexity.
`rust_test_start(path)` finds the first `#[cfg(test)]` line; functions starting
after it are dropped when `skip_rust_tests` is on. The convention that the test
module closes the file holds across this repository; a test module in the
middle of a file would hide the production code after it, which is why the
success line prints how many functions were skipped as tests.

`run_lizard` gives its child a wall-clock ceiling, `LIZARD_TIMEOUT_SECONDS`
(default 600s, overridable via the environment variable of the same name) —
the same shape and name `scripts/run-core-tests.sh` uses for
`CORE_TEST_TIMEOUT_SECONDS`. A lizard that hangs is turned into a `LizardError`
naming the ceiling instead of blocking the whole preflight before anything is
built.
"""

import csv
import io
import os
import re
import shutil
import subprocess

TEST_ATTRIBUTE = re.compile(r"^\s*#\[cfg\(test\)\]")
LIZARD_TIMEOUT_SECONDS = int(os.environ.get("LIZARD_TIMEOUT_SECONDS", "600"))


class LizardError(Exception):
    """lizard is missing or refused to run; printed as FAIL, exit 2."""


def rust_test_start(path):
    """The line of the first `#[cfg(test)]` in `path`, or None."""
    try:
        with open(path, errors="replace") as handle:
            for number, line in enumerate(handle, 1):
                if TEST_ATTRIBUTE.match(line):
                    return number
    except OSError:
        return None
    return None


def _lizard_csv(paths, languages, excludes):
    """One lizard --csv run over `paths`, or a LizardError."""
    command = ["lizard", "--csv"]
    for language in languages:
        command += ["-l", language]
    for pattern in excludes:
        command += ["-x", pattern]
    command += list(paths)
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=LIZARD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise LizardError("lizard ran past its %ds time limit — ended" % LIZARD_TIMEOUT_SECONDS)
    if proc.returncode not in (0, 1):  # 1 is lizard's own "over its thresholds" — not ours to act on
        raise LizardError("lizard exited %d: %s" % (proc.returncode, proc.stderr.strip()[:300]))
    return proc.stdout


def run_lizard(sources, languages, excludes, exclude_except=None):
    """lizard's CSV over `sources` with `excludes` applied, plus — for `exclude_except`,
    paths an exclude glob would otherwise drop by name but that are production code —
    a second pass over exactly those paths with no exclude at all. Raises LizardError."""
    if not shutil.which("lizard"):
        raise LizardError("lizard is not installed — brew install lizard (or pip install lizard)")
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
    test_starts = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 11:
            continue
        try:
            cc, length, start, end = int(row[1]), int(row[4]), int(row[9]), int(row[10])
        except ValueError:
            continue  # a header line, if lizard ever prints one
        path = os.path.realpath(row[6])
        if skip_rust_tests and path.endswith(".rs"):
            if path not in test_starts:
                test_starts[path] = rust_test_start(path)
            boundary = test_starts[path]
            if boundary is not None and start >= boundary:
                skipped += 1
                continue
        functions.append(Function(path, start, end, cc, length, row[7]))
    return functions, skipped


def complexities(functions):
    """{(realpath, line): cc} — the shape the CRAP judge joins on."""
    return {(f.path, f.line): f.cc for f in functions}

#!/usr/bin/env python3
"""mutate — mutation testing over a host-less Swift package.

A test suite that passes says the code does what the tests expect; it does not
say the tests expect enough. Mutation testing asks the second question: flip one
operator in the source — `<` to `<=`, `==` to `!=`, `&&` to `||`, `true` to
`false`, drop a `!` — run the suite, and expect red. A mutant the suite lets
through is a path no test pins down, and it is named here by file, line and
the flip that survived, so somebody can write the test that kills it. This is
the "hardener" stage Uncle Bob runs over agent-written code; it was impractical
when a suite took minutes, and a host-less package's takes seconds.

Scope: the package and source root named by the `mutation` section of
`quality.json` (`package`, `sources`; `--package` and `--sources` override it),
because a package with no test host builds and tests in seconds. Each mutant is
applied to the real file, `swift test` runs in the package, and the original is
written back — whatever happened, including an interrupt. The tree must be
clean for the files being mutated, so nothing of yours is ever mixed into a
mutant; the script refuses otherwise. A mutant that fails to compile is not a
survivor and not a kill; it is counted as `uncompilable` and left out of the
score.

The operators this harness flips are exactly the ones that can turn a suite
non-terminating — `<` widened to `<=` on a loop bound, a `!` dropped from a wait
condition, `true` narrowed to `false` on an exit flag — so a suite run carries
a wall-clock ceiling, `MUTATE_TIMEOUT_SECONDS` (default 600s, overridable via
the environment variable of the same name; same shape as
`extractors/complexity.py`'s `LIZARD_TIMEOUT_SECONDS`). A mutant whose suite runs past
it is ended, counted as `timed-out` alongside `uncompilable` — out of the
score, never a survivor — and the source is restored the same as after a kill.
The green-before-mutation run gets the same ceiling; a timeout there is a FAIL,
not a hang.

Each mutant is judged narrowly first: the test classes whose file is named
after the source (`Foo.swift` → `FooTests.swift`, `FooDocumentTests.swift`)
run alone through `swift test --filter`, and only a mutant those let through
goes to the whole suite. A kill is a kill either way; a survivor is one the
whole suite let through. That is what makes a run over the package minutes
rather than hours, and it is logged per mutant — `killed (narrow)`,
`killed (full)`, `survived` — so the reading is never ambiguous. A source
with no test file of its own goes straight to the whole suite.

It still runs `swift test` many times, so it is not a preflight step. Run it
on the file you just wrote, or on the whole package when reviewing.

  quality/bin/mutate.py Services/Parser.swift       # one file (path under the sources root)
  quality/bin/mutate.py --all                               # every source file
  quality/bin/mutate.py --list Services/Parser.swift # print the mutants, run nothing
  quality/bin/mutate.py --config quality.json FILE          # a particular quality.json (the tests use this)
  quality/bin/mutate.py --package DIR --sources DIR FILE    # another package, no config needed
"""

import argparse
import os
import re
import signal
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config

# (pattern, replacement, name). Patterns are applied to code only — comments and
# string literals are masked first — and each match is its own mutant.
OPERATORS = [
    # Binary operators only where written as operators — a space on each side —
    # so `Array<String>`, `->` and `a<b` in a generic are never touched.
    (r"(?<= )==(?= )", "!=", "== → !="),
    (r"(?<= )!=(?= )", "==", "!= → =="),
    (r"(?<= )<=(?= )", "<", "<= → <"),
    (r"(?<= )>=(?= )", ">", ">= → >"),
    (r"(?<= )<(?= )", "<=", "< → <="),
    (r"(?<= )>(?= )", ">=", "> → >="),
    (r"(?<= )&&(?= )", "||", "&& → ||"),
    (r"(?<= )\|\|(?= )", "&&", "|| → &&"),
    (r"\btrue\b", "false", "true → false"),
    (r"\bfalse\b", "true", "false → true"),
    # `!x` → `x`; not `!=`, not `x!`, not `try!`
    (r"(?<![!=A-Za-z0-9_)\]])!(?=[A-Za-z_(])", "", "drop !"),
]


def mask(text):
    """Comments and string literals blanked (offsets kept), so no mutant lands in prose."""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    text = re.sub(r'"""(?:.|\n)*?"""', blank, text)
    text = re.sub(r'"(?:\\.|[^"\\\n])*"', blank, text)
    text = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    text = re.sub(r"//[^\n]*", blank, text)
    return text


def mutants_of(text):
    """(start, end, replacement, name, line) for every single-operator mutant of `text`."""
    masked = mask(text)
    out = []
    for pattern, replacement, name in OPERATORS:
        for m in re.finditer(pattern, masked):
            line = masked.count("\n", 0, m.start()) + 1
            out.append((m.start(), m.end(), replacement, name, line))
    out.sort()
    return out


def test_classes_for(sources, relative, tests_root):
    """The test classes (XCTest or swift-testing suites) in files named after the
    source — `Foo.swift` → `FooTests.swift`, `FooDocumentTests.swift`."""
    stem = os.path.basename(relative)[:-len(".swift")]
    classes = []
    for dirpath, _, filenames in os.walk(tests_root):
        for name in filenames:
            if name.startswith(stem) and name.endswith("Tests.swift"):
                with open(os.path.join(dirpath, name), errors="replace") as handle:
                    classes += re.findall(r"^(?:final class|class|struct) (\w+)", mask(handle.read()), re.M)
    return sorted(set(classes))


# Set from `mutation.test_command` / `mutation.filter_flag` in quality.json when the
# package cannot be tested with `swift test` on the host.
SUITE = {"test_command": None, "filter_flag": None}

MUTATE_TIMEOUT_SECONDS = int(os.environ.get("MUTATE_TIMEOUT_SECONDS", "600"))


UNRUN_RE = re.compile(r"Executed 0 tests|\b0 tests? (?:ran|executed|passed)|No matching test cases")


def _did_not_compile(returncode, out):
    return returncode != 0 and "error:" in out and "Compiling" in out and "Test Suite" not in out and "Executed" not in out


def verdict_of(returncode, out, filtered):
    """What one suite run says about a mutant: `killed`, `survived`, `uncompilable` — or
    `unrun`, a filtered run that executed no tests at all (a filter the runner did not
    match: exit 0 with nothing run is not survival, and the full suite decides)."""
    if _did_not_compile(returncode, out):
        return "uncompilable"
    if returncode != 0:
        return "killed"
    return "unrun" if filtered and UNRUN_RE.search(out) else "survived"


def run_suite(package, filters=None, test_command=None, filter_flag=None):
    """One run of the suite; `test_command` replaces `swift test` for a package whose
    app target does not build on the host (an iOS package tested through
    `xcodebuild test` on a simulator), and `filter_flag` is that runner's spelling
    of a test-class filter with `{name}` in it — `-only-testing:AcmeTests/{name}`.
    A run past `MUTATE_TIMEOUT_SECONDS` is ended and reported as `timed-out`
    rather than left to hang the caller."""
    test_command = test_command or SUITE["test_command"]
    filter_flag = filter_flag or SUITE["filter_flag"]
    command = list(test_command) if test_command else ["swift", "test"]
    for name in filters or []:
        command += [filter_flag.format(name=name)] if filter_flag else ["--filter", name]
    try:
        proc = subprocess.run(command, cwd=package, capture_output=True, text=True, timeout=MUTATE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return "timed-out"
    return verdict_of(proc.returncode, proc.stdout + proc.stderr, bool(filters))


def mutate_file(package, sources, relative, list_only=False, log=print, tests_root=None):
    path = os.path.join(sources, relative)
    narrow = test_classes_for(sources, relative, tests_root) if tests_root else []
    with open(path) as handle:
        original = handle.read()
    mutants = mutants_of(original)
    if list_only:
        for start, end, replacement, name, line in mutants:
            log("  %s:%d  %s  (%r → %r)" % (relative, line, name, original[start:end], replacement))
        return {"killed": 0, "survived": [], "uncompilable": 0, "timed_out": 0, "total": len(mutants)}
    result = {"killed": 0, "survived": [], "uncompilable": 0, "timed_out": 0, "total": len(mutants)}

    def restore(*_):
        with open(path, "w") as handle:
            handle.write(original)

    previous = signal.signal(signal.SIGINT, lambda *a: (restore(), sys.exit(130)))
    try:
        for index, (start, end, replacement, name, line) in enumerate(mutants, 1):
            mutated = original[:start] + replacement + original[end:]
            with open(path, "w") as handle:
                handle.write(mutated)
            try:
                how = "full"
                verdict = run_suite(package, narrow) if narrow else run_suite(package)
                if narrow and verdict == "killed":
                    how = "narrow"
                elif narrow and verdict in ("survived", "unrun"):
                    verdict = run_suite(package)
            finally:
                restore()
            if verdict == "killed":
                result["killed"] += 1
            elif verdict == "survived":
                result["survived"].append((line, name, original[start:end]))
            elif verdict == "timed-out":
                result["timed_out"] += 1
            else:
                result["uncompilable"] += 1
            log("  [%d/%d] %s:%d %s — %s" % (index, len(mutants), relative, line, name,
                                              verdict + (" (%s)" % how if verdict == "killed" else "")))
    finally:
        signal.signal(signal.SIGINT, previous)
    return result


def clean_in_git(repo, paths):
    proc = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=repo, capture_output=True, text=True)
    return proc.returncode == 0 and proc.stdout.strip() == ""


def resolve_roots(args):
    """The package and sources directories: the flags when given, else the `mutation`
    section of quality.json. The config is only read for what the flags leave out."""
    package, sources = args.package, args.sources
    if package is None or sources is None:
        config = quality_config.load(args.config)

        def resolve(key):  # relative to quality.json's directory; `~` and absolute paths pass through
            return os.path.join(config.root, os.path.expanduser(config.get("mutation", key)))
        package = package or resolve("package")
        sources = sources or resolve("sources")
        section = config.section("mutation")
        SUITE["test_command"] = section.get("test_command")
        SUITE["filter_flag"] = section.get("filter_flag")
    return os.path.abspath(package), os.path.abspath(sources)


def main():
    parser = argparse.ArgumentParser(description="mutation testing over a host-less Swift package")
    parser.add_argument("files", nargs="*", help="paths under the sources root")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--list", action="store_true", help="print the mutants, run nothing")
    parser.add_argument("--package", default=None, help="the package directory (default: quality.json's mutation.package)")
    parser.add_argument("--sources", default=None, help="its source root (default: quality.json's mutation.sources)")
    parser.add_argument("--tests", default=None, help="the test tree for narrowing (default: the package's Tests/)")
    parser.add_argument("--no-narrow", action="store_true", help="judge every mutant with the whole suite")
    parser.add_argument("--no-git-check", action="store_true", help="skip the clean-tree refusal (the tests use this)")
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    package, sources = resolve_roots(args)
    tests_root = None if args.no_narrow else os.path.abspath(args.tests or os.path.join(package, "Tests"))
    files = args.files
    if args.all:
        files = sorted(os.path.relpath(os.path.join(dp, f), sources)
                       for dp, _, fs in os.walk(sources) for f in fs if f.endswith(".swift"))
    if not files:
        parser.error("name a file under the sources root (%s), or --all" % sources)
    for relative in files:
        if not os.path.isfile(os.path.join(sources, relative)):
            print("FAIL: no such source file: %s" % relative, file=sys.stderr)
            return 2
    if not args.list and not args.no_git_check:
        full = [os.path.join(sources, f) for f in files]
        if not clean_in_git(package, full):
            print("FAIL: uncommitted changes in the files to mutate — commit or stash first, so a mutant never mixes with your edit", file=sys.stderr)
            return 2
    if not args.list:
        # a suite that is already red kills every mutant for the wrong reason
        pre = run_suite(package)
        if pre == "timed-out":
            print("FAIL: swift test ran past its %ds time limit before any mutation was applied — ended"
                  % MUTATE_TIMEOUT_SECONDS, file=sys.stderr)
            return 2
        if pre != "survived":
            print("FAIL: swift test is not green before any mutation; fix that first", file=sys.stderr)
            return 2
    totals = {"killed": 0, "survived": 0, "uncompilable": 0, "timed_out": 0, "total": 0}
    survivors = []
    for relative in files:
        print("%s:" % relative)
        result = mutate_file(package, sources, relative, list_only=args.list, tests_root=tests_root)
        totals["killed"] += result["killed"]
        totals["survived"] += len(result["survived"])
        totals["uncompilable"] += result["uncompilable"]
        totals["timed_out"] += result["timed_out"]
        totals["total"] += result["total"]
        survivors.extend((relative, line, name, text) for line, name, text in result["survived"])
    if args.list:
        print("%d mutants across %d file(s)" % (totals["total"], len(files)))
        return 0
    scored = totals["killed"] + totals["survived"]
    score = (100.0 * totals["killed"] / scored) if scored else 100.0
    print()
    print("mutation score %.0f%% — %d killed, %d survived, %d uncompilable, %d timed out, of %d mutants in %d file(s)"
          % (score, totals["killed"], totals["survived"], totals["uncompilable"], totals["timed_out"], totals["total"], len(files)))
    if survivors:
        print("survivors — each is a path no test pins down:")
        for relative, line, name, text in survivors:
            print("  %s:%d  %s  (%r)" % (relative, line, name, text))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())

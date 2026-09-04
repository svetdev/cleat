#!/usr/bin/env python3
"""test-mutate — assert quality/bin/mutate.py mutates, judges, narrows, refuses and restores.

Driven against a throwaway package with one function and one test that pins
down some of its branches and not others, through a quality.json written beside
it: the mutants are what the operator table says they are and nothing lands in
a comment or a string; the survivor is the untested branch and the killed ones
are the tested ones — narrowly when the source's own test file kills them, by
the full suite when only another file's does; the file is byte-for-byte what it
was afterwards; a red suite is refused before any mutation; a file with
uncommitted changes is refused before the suite even runs; and --list runs
nothing. --list is driven through the explicit --package/--sources flags so
both ways of naming the package are covered. It runs `swift test` in the
fixture a handful of times (the fixture has no host), and writes nothing
outside a directory under the user's cache that it removes on exit.

  quality/tests/test-mutate.py
"""
import json, os, re, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "mutate.py")
failed = 0
def check(name, ok, detail=""):
    global failed
    print("  %s  %s" % ("ok  " if ok else "FAIL", name) + ("" if ok else "\n          " + detail)); failed += 0 if ok else 1
def write(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as h: h.write(text)

# --- what a suite run says, without running one
sys.path.insert(0, os.path.dirname(SCRIPT))
import mutate
check("a filtered run that executed no tests is unrun, not survived — the full suite decides", mutate.verdict_of(0, "Test Suite 'Selected tests' passed\n\t Executed 0 tests, with 0 failures\n", True) == "unrun")
check("a filtered run that ran tests and passed is survival", mutate.verdict_of(0, "Executed 3 tests, with 0 failures", True) == "survived")
check("an unfiltered run that ran nothing is still survival — there is nothing wider to fall back to", mutate.verdict_of(0, "Executed 0 tests", False) == "survived")
check("a failing run is a kill", mutate.verdict_of(1, "Executed 3 tests, with 1 failure", True) == "killed")
check("a build failure is uncompilable", mutate.verdict_of(1, "Compiling Fixture\nerror: cannot convert", False) == "uncompilable")

cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), "Library", "Caches")
os.makedirs(cache, exist_ok=True)
tmp = tempfile.mkdtemp(prefix="mutate.", dir=cache)
try:
    pkg = os.path.join(tmp, "Fixture"); src = os.path.join(pkg, "Sources", "Fixture"); tests = os.path.join(pkg, "Tests", "FixtureTests")
    config = os.path.join(tmp, "quality.json")
    write(config, json.dumps({"mutation": {"package": "Fixture", "sources": "Fixture/Sources/Fixture"}}))
    write(os.path.join(pkg, "Package.swift"), '''// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "Fixture", platforms: [.macOS(.v14)],
    targets: [.target(name: "Fixture"), .testTarget(name: "FixtureTests", dependencies: ["Fixture"])])
''')
    source = '''// a < b in a comment must not be mutated
package enum Gate {
    /// `x == 3` in prose either
    package static func open(_ x: Int, force: Bool) -> Bool {
        let label = "never > this string"
        _ = label
        if x < 0 { return false }             // the untested branch: no test passes a negative
        if force && x > 100 { return true }   // tested only by ForcingTests, a file not named after Gate
        return x == 3
    }
}
'''
    write(os.path.join(src, "Gate.swift"), source)
    write(os.path.join(tests, "GateTests.swift"), '''import XCTest
@testable import Fixture
final class GateTests: XCTestCase {
    func testOpensOnlyAtThree() {
        XCTAssertTrue(Gate.open(3, force: false))
        XCTAssertFalse(Gate.open(4, force: false))
        XCTAssertFalse(Gate.open(2, force: false))
    }
}
''')
    # A test in a file NOT named after the source: the narrow pass cannot see it,
    # so the mutant it kills must be reported as killed by the full suite.
    write(os.path.join(tests, "ForcingTests.swift"), '''import XCTest
@testable import Fixture
final class ForcingTests: XCTestCase {
    func testForceOpensAboveAHundred() {
        XCTAssertTrue(Gate.open(101, force: true))
        XCTAssertFalse(Gate.open(100, force: true))
    }
}
''')
    def run(*args, roots=("--config", config), env=None):
        p = subprocess.run([sys.executable, SCRIPT, *roots, *args], capture_output=True, text=True,
                            env={**os.environ, **env} if env else None)
        return p.returncode, p.stdout + p.stderr
    expected = [("7", "< → <="), ("7", "false → true"), ("8", "&& → ||"), ("8", "> → >="), ("8", "true → false"), ("9", "== → !=")]
    code, out = run("--list", "Gate.swift", roots=("--package", pkg, "--sources", src))
    names = re.findall(r"Gate\.swift:(\d+)  (.+?)  \(", out)
    check("--list over explicit --package/--sources names every operator mutant with its line", names == expected, out)
    check("nothing in a comment or a string is a mutant", "line 1" not in out and ":3 " not in out and ":5 " not in out, out)
    check("--list runs nothing and exits 0", code == 0, out)
    code, out = run("--list", "Gate.swift")
    check("--config resolves the package and sources from quality.json's mutation section", code == 0 and re.findall(r"Gate\.swift:(\d+)  (.+?)  \(", out) == expected, out)
    before = open(os.path.join(src, "Gate.swift")).read()
    code, out = run("--no-git-check", "Gate.swift")
    check("the untested branch's mutants survive", "Gate.swift:7  < → <=" in out and "Gate.swift:7  false → true" in out, out)
    check("a mutant the source's own test file kills is killed narrowly", "== → != — killed (narrow)" in out, out)
    check("a mutant only another file's test kills is killed by the full suite", "> → >= — killed (full)" in out and "&& → || — killed (full)" in out, out)
    check("the score counts both", "4 killed, 2 survived, 0 uncompilable, 0 timed out, of 6 mutants" in out, out)
    check("survivors make the exit status 1", code == 1, out)
    check("the file is restored byte for byte", open(os.path.join(src, "Gate.swift")).read() == before, "file differs")
    # a red suite is refused before any mutation
    write(os.path.join(tests, "GateTests.swift"), open(os.path.join(tests, "GateTests.swift")).read().replace("XCTAssertFalse(Gate.open(4", "XCTAssertTrue(Gate.open(4"))
    code, out = run("--no-git-check", "Gate.swift")
    check("a suite that is already red is refused", code == 2 and "not green before any mutation" in out, out)
    check("and the file is untouched", open(os.path.join(src, "Gate.swift")).read() == before, "file differs")
    # the clean-tree refusal: a mutant must never mix with an uncommitted edit
    git = ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], cwd=pkg, check=True, capture_output=True)
    subprocess.run([*git, "add", "."], cwd=pkg, check=True, capture_output=True)
    subprocess.run([*git, "commit", "-q", "-m", "fixture"], cwd=pkg, check=True, capture_output=True)
    code, out = run("Gate.swift")
    check("a committed file passes the clean-tree gate", "uncommitted changes" not in out and "not green before any mutation" in out, out)
    write(os.path.join(src, "Gate.swift"), before + "// an edit in progress\n")
    code, out = run("Gate.swift")
    check("a file with uncommitted changes is refused before the suite runs", code == 2 and "uncommitted changes in the files to mutate" in out and "not green" not in out, out)
    check("and left as it was", open(os.path.join(src, "Gate.swift")).read() == before + "// an edit in progress\n", "file differs")

    # A mutant whose suite never returns: a test_command driven by a stub runner that
    # sleeps only once the mutation lands, so the green-before-mutation run stays fast.
    runner = os.path.join(tmp, "runner.py")
    write(runner, "import sys, time\n"
                  "with open(sys.argv[1]) as h:\n"
                  "    text = h.read()\n"
                  "if 'false' in text:\n"
                  "    time.sleep(5)\n")
    tpkg = os.path.join(tmp, "TimeoutFixture")
    tsrc = os.path.join(tpkg, "Sources", "TimeoutFixture")
    write(os.path.join(tsrc, "Flag.swift"), '''package enum Flag {
    package static func on() -> Bool { true }
}
''')
    before_flag = open(os.path.join(tsrc, "Flag.swift")).read()
    timeout_config = os.path.join(tmp, "timeout-quality.json")
    write(timeout_config, json.dumps({"mutation": {
        "package": "TimeoutFixture",
        "sources": "TimeoutFixture/Sources/TimeoutFixture",
        "test_command": [sys.executable, runner, "Sources/TimeoutFixture/Flag.swift"],
    }}))
    code, out = run("--no-git-check", "Flag.swift", roots=("--config", timeout_config), env={"MUTATE_TIMEOUT_SECONDS": "1"})
    check("a mutant whose suite never exits is timed out, named in the per-mutant line", "timed-out" in out, out)
    check("a timed-out mutant is counted in neither killed nor survived",
          "0 killed, 0 survived, 0 uncompilable, 1 timed out, of 1 mutants" in out, out)
    check("a timed-out mutant is not a survivor and does not fail the run", code == 0 and "survivors" not in out, out)
    check("the mutated file is restored byte-for-byte after a timeout",
          open(os.path.join(tsrc, "Flag.swift")).read() == before_flag, "file differs")

    # A timeout on the green-before-mutation run itself: the FAIL names the ceiling.
    always_sleep = os.path.join(tmp, "always_sleep.py")
    write(always_sleep, "import time\ntime.sleep(5)\n")
    green_timeout_config = os.path.join(tmp, "green-timeout-quality.json")
    write(green_timeout_config, json.dumps({"mutation": {
        "package": "TimeoutFixture",
        "sources": "TimeoutFixture/Sources/TimeoutFixture",
        "test_command": [sys.executable, always_sleep],
    }}))
    code, out = run("--no-git-check", "Flag.swift", roots=("--config", green_timeout_config), env={"MUTATE_TIMEOUT_SECONDS": "1"})
    check("a green-before-mutation run that never exits fails naming the ceiling instead of hanging",
          code == 2 and "1s time limit" in out and "before any mutation" in out, out)
    check("and the file is untouched", open(os.path.join(tsrc, "Flag.swift")).read() == before_flag, "file differs")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print()
if failed: print("test-mutate: %d case(s) failed." % failed); sys.exit(1)
print("test-mutate: all cases passed.")

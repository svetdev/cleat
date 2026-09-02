#!/usr/bin/env python3
"""test-check-crap — assert the score, the readers, the join, the ratchet and the config in quality/bin/check-crap.py.

Driven with files it writes: a SwiftLint report, an xccov report, an llvm-cov
export and a source tree, so every reading is pinned — the formula at its
corners, a function matched to its coverage by file and line (and found one
line off, where an attribute shifts one reader), a function with no coverage
record read as uncovered, a new offender failing and named, a baselined one
passing, --write-baseline writing what is over the gate. Then the same tree
under a fixture quality.json with only --config: the threshold, the baseline
and the reported paths come from the file; an empty bundle or export glob is
named in the failure; a missing key is named. Then a swiftlint and an xcrun
that never return, each ended at a ceiling the test lowers on the imported
module. Starts no build, runs no real swiftlint (only a stub standing in for
one, for the timeout cases), writes nothing outside a temporary directory.

  quality/tests/test-check-crap.py
"""
import importlib.util, json, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "check-crap.py")
spec = importlib.util.spec_from_file_location("check_crap", SCRIPT); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
sys.path.insert(0, os.path.dirname(SCRIPT))
import ratchet
from extractors import complexity, coverage
failed = 0
def check(name, ok, detail=""):
    global failed
    print("  %s  %s" % ("ok  " if ok else "FAIL", name) + ("" if ok else "\n          " + detail)); failed += 0 if ok else 1
def write(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as h: h.write(data if isinstance(data, str) else json.dumps(data))

check("crap: a fully covered function scores its complexity", mod.crap(8, 1.0) == 8.0)
check("crap: an uncovered function scores about its complexity squared", mod.crap(8, 0.0) == 72.0)
check("crap: half coverage on cc 8 is 16", abs(mod.crap(8, 0.5) - 16.0) < 1e-9)
check("crap: cc 3 uncovered is over the gate of 8", mod.crap(3, 0.0) > 8)

tmp = tempfile.mkdtemp(prefix="check-crap-")
try:
    app = os.path.join(tmp, "Acme", "Acme"); pkg = os.path.join(tmp, "Acme", "AcmeCore", "Sources", "AcmeCore")
    knot = os.path.join(app, "Services", "Knot.swift"); core = os.path.join(pkg, "Services", "Core.swift")
    write(knot, "import Foundation\n\n@MainActor\nfunc knotted(_ a: Int) -> Int { a }\n\nfunc tidy(_ a: Int) -> Int { a }\n\nfunc orphan(_ a: Int) -> Int { a }\n\nfunc wide(\n    _ a: Int,\n    _ b: Int\n) -> Int {\n    a + b\n}\n")
    write(core, "package func twisted(_ a: Int) -> Int { a }\n\npackage func thunked(\n    _ a: Int,\n    b: Int = 1\n) -> Int {\n    a + b\n}\n\npackage func braced(\n    _ a: Int,\n    f: () -> Int = { 1 }\n) -> Int {\n    a + f()\n}\n")
    lint = [{"file": knot, "line": 4, "reason": "Function should have complexity 1 or less; currently complexity is 9"},
            {"file": knot, "line": 6, "reason": "Function should have complexity 1 or less; currently complexity is 8"},
            {"file": knot, "line": 8, "reason": "Function should have complexity 1 or less; currently complexity is 4"},
            {"file": knot, "line": 10, "reason": "Function should have complexity 1 or less; currently complexity is 8"},
            {"file": core, "line": 1, "reason": "Function should have complexity 1 or less; currently complexity is 6"},
            {"file": core, "line": 3, "reason": "Function should have complexity 1 or less; currently complexity is 6"},
            {"file": core, "line": 10, "reason": "Function should have complexity 1 or less; currently complexity is 6"}]
    # xccov: knotted's coverage record sits on the attribute line (3), tidy's on its own line, orphan has none
    xccov = {"targets": [{"files": [{"path": knot, "functions": [
        {"name": "knotted", "lineNumber": 3, "executableLines": 10, "coveredLines": 1},
        {"name": "tidy", "lineNumber": 6, "executableLines": 10, "coveredLines": 10},
        {"name": "wide", "lineNumber": 13, "executableLines": 10, "coveredLines": 10}]},
        # the package file, as the app bundle records it: 0% at the thunk's line. Its root
        # Acme/AcmeCore begins with the app root Acme/Acme, and is not under it.
        {"path": core, "functions": [{"name": "thunked", "lineNumber": 5, "executableLines": 4, "coveredLines": 0}]}]}]}
    # llvm-cov: twisted has four regions, one ran
    codecov = {"data": [{"functions": [{"name": "$twisted", "filenames": [core],
        "regions": [[1, 1, 1, 40, 1, 0, 0, 0], [1, 20, 1, 30, 0, 0, 0, 0], [1, 31, 1, 35, 0, 0, 0, 0], [1, 36, 1, 40, 0, 0, 0, 0]]},
        # thunked: the default argument's thunk — the body's mangled name plus `fA0_`, one region at line 5 —
        # never ran, every caller passed b; the body at the brace line (6) ran in full and is the function
        {"name": "$s4Core7thunkedFfA0_", "filenames": [core], "regions": [[5, 12, 5, 13, 0, 0, 0, 0]]},
        {"name": "$s4Core7thunkedF", "filenames": [core], "regions": [[6, 10, 8, 2, 1, 0, 0, 0], [7, 5, 7, 10, 1, 0, 0, 0], [7, 5, 7, 10, 1, 0, 0, 0]]},
        # braced: the default argument is a closure — a `{` inside the signature, at line 12 —
        # and the body opens at line 13, past it; the walk down must not stop at the closure
        {"name": "$s4Core6bracedFfA1_yScfU_", "filenames": [core], "regions": [[12, 21, 12, 26, 0, 0, 0, 0]]},
        {"name": "$s4Core6bracedF", "filenames": [core], "regions": [[13, 10, 15, 2, 1, 0, 0, 0], [14, 5, 14, 12, 1, 0, 0, 0], [14, 5, 14, 12, 1, 0, 0, 0]]}]}]}
    paths = dict(lint=os.path.join(tmp, "lint.json"), xccov=os.path.join(tmp, "xccov.json"), codecov=os.path.join(tmp, "codecov.json"), baseline=os.path.join(tmp, "baseline.json"))
    write(paths["lint"], lint); write(paths["xccov"], xccov); write(paths["codecov"], codecov)
    # no quality.json anywhere above the temporary tree: a fully flagged run must not need one
    nowhere = os.path.join(tmp, "nowhere"); os.makedirs(nowhere)
    def run(*args, cwd=nowhere):
        p = subprocess.run([sys.executable, SCRIPT, "--lint", paths["lint"], "--xccov", paths["xccov"], "--codecov", paths["codecov"],
                            "--baseline", paths["baseline"], "--app-sources", app, "--package-sources", pkg, "--repo", tmp, "--threshold", "8", *args],
                           capture_output=True, text=True, cwd=cwd)
        return p.returncode, p.stdout + p.stderr
    code, out = run()
    check("offenders fail the check", code == 1, out)
    p = subprocess.run([sys.executable, SCRIPT, "--lint", paths["lint"], "--xccov", paths["xccov"], "--baseline", paths["baseline"],
                        "--app-sources", app, "--repo", tmp, "--threshold", "8"], capture_output=True, text=True, cwd=nowhere)
    check("xccov alone: the package file it names outside the app root is dropped with a NOTE steering to llvm_cov",
          "NOTE: the xccov report names 1 Swift file(s) outside the app root" in p.stdout and "llvm_cov" in p.stdout, p.stdout + p.stderr)
    check("with the package's llvm-cov export read too, no such note", "NOTE: the xccov report names" not in out, out)
    check("a fully flagged run opens no quality.json", "quality.json" not in out, out)
    check("the barely-covered complex function is named with its score", "Knot.swift:4  crap 68 (cc 9, coverage 10%)" in out, out)
    check("a coverage record one line off (an attribute line) is still matched", "coverage 10%" in out and "coverage 0%)  @MainActor" not in out, out)
    check("a fully covered function at the gate's complexity passes", "Knot.swift:6" not in out, out)
    check("a function with no coverage record is uncovered — not its neighbour's — and fails", "Knot.swift:8  crap 20 (cc 4, coverage 0%)" in out, out)
    check("neither a never-run default-argument thunk nor the app bundle's record of a package file (a root that shares the app root's prefix) hides a fully run body", "Core.swift:3" not in out, out)
    check("a default closure's brace inside the signature does not end the walk to the body", "Core.swift:10" not in out, out)
    check("the package function is read from llvm-cov regions", "Core.swift:1  crap 21 (cc 6, coverage 25%)" in out, out)
    check("a multi-line signature is matched to the record on its opening-brace line", "Knot.swift:10" not in out, out)
    check("the baseline count is in the message", "beyond the 0 the baseline holds" in out, out)
    code, out = run("--write-baseline")
    check("--write-baseline accepts what is over the gate", code == 0 and "3 function(s) over CRAP 8" in out, out)
    entries, _ = ratchet.read(paths["baseline"])
    check("the baseline keys by file and declaration text", sorted(e["text"] for e in entries) == sorted(["func knotted(_ a: Int) -> Int { a }", "func orphan(_ a: Int) -> Int { a }", "package func twisted(_ a: Int) -> Int { a }"]), str(entries))
    check("the baseline's paths are relative to --repo", sorted(e["file"] for e in entries) == ["Acme/Acme/Services/Knot.swift", "Acme/Acme/Services/Knot.swift", "Acme/AcmeCore/Sources/AcmeCore/Services/Core.swift"], str(entries))
    code, out = run()
    check("with everything baselined the check passes", code == 0, out)
    check("and the success line carries the counts", "7 functions judged, 3 over CRAP 8, all 3 in the baseline" in out, out)
    code, out = run("--quiet")
    check("--quiet prints nothing on success", out == "", repr(out))
    # a shifted line still matches its baseline entry by text
    write(knot, "import Foundation\n// a new comment\n\n@MainActor\nfunc knotted(_ a: Int) -> Int { a }\n\nfunc tidy(_ a: Int) -> Int { a }\n\nfunc orphan(_ a: Int) -> Int { a }\n\nfunc wide(\n    _ a: Int,\n    _ b: Int\n) -> Int {\n    a + b\n}\n")
    for v in lint:
        if v["file"] == knot: v["line"] += 1
    xccov["targets"][0]["files"][0]["functions"][0]["lineNumber"] = 4; xccov["targets"][0]["files"][0]["functions"][1]["lineNumber"] = 7; xccov["targets"][0]["files"][0]["functions"][2]["lineNumber"] = 14
    write(paths["lint"], lint); write(paths["xccov"], xccov)
    code, out = run()
    check("a baselined function that moved a line still passes", code == 0, out)
    check("a baseline every entry of which matched prints no note at all", "NOTE" not in out, out)

    # ---- worsened: a baselined function whose CRAP rose fails; its key still matches, its score does not
    lint[0]["reason"] = lint[0]["reason"].replace("complexity is 9", "complexity is 12")
    write(paths["lint"], lint)
    code, out = run()
    check("a baselined function that got worse fails", code == 1, out)
    check("it is reported as worse, with both scores", "got worse" in out and "crap 117 (cc 12, coverage 10%), was crap 68.0" in out, out)
    check("the failure output names the fix, never the accept command", "--write-baseline" not in out and "Cover the untested paths" in out, out)

    # ---- improved: the baseline records more than the code has — a NOTE, and a --strict failure
    run("--write-baseline")
    lint[0]["reason"] = lint[0]["reason"].replace("complexity is 12", "complexity is 9")
    write(paths["lint"], lint)
    code, out = run()
    check("a baselined function that improved still passes", code == 0, out)
    check("the improvement is noted, with the tightening command", "improved" in out and "baseline says crap 117.0" in out and "--write-baseline" in out, out)
    code, out = run("--strict")
    check("--strict refuses a baseline looser than the code", code == 1 and "looser than the code" in out, out)
    run("--write-baseline")
    code, out = run("--strict")
    check("once tightened, --strict passes", code == 0, out)

    # ---- a baseline entry matching nothing this run: staleness, reported but never a failure
    stale_entries, _ = ratchet.read(paths["baseline"])
    stale_entries.append({"file": "Acme/Acme/Services/Ghost.swift", "text": "func ghost() -> Int { 0 }",
                           "cc": 5, "coverage": 0.1, "crap": 42.3})
    write(paths["baseline"], stale_entries)
    code, out = run()
    check("a stale baseline entry does not fail the run", code == 0, out)
    check("the stale entry is named by file, declaration text and recorded score",
          "Ghost.swift  crap 42.3  func ghost() -> Int { 0 }" in out, out)
    check("the stale note follows the success line", "all 4 in the baseline" in out and out.index("all 4 in the baseline") < out.index("Ghost.swift"), out)
    code, out = run("--quiet")
    check("--quiet suppresses the success line but not the stale note", "OK:" not in out and "Ghost.swift" in out, out)

    # a genuinely new offender still fails and still lists it, alongside the stale entry
    write(core, "package func twisted(_ a: Int) -> Int { a }\n\npackage func thunked(\n    _ a: Int,\n    b: Int = 1\n) -> Int {\n    a + b\n}\n\npackage func braced(\n    _ a: Int,\n    f: () -> Int = { 1 }\n) -> Int {\n    a + f()\n}\n\npackage func fresh(_ a: Int) -> Int { a }\n")
    lint.append({"file": core, "line": 17, "reason": "Function should have complexity 1 or less; currently complexity is 9"})
    write(paths["lint"], lint)
    code, out = run()
    check("a genuinely new offender still fails the run", code == 1, out)
    check("the new offender is still named", "Core.swift:17" in out, out)
    check("the stale entry is still named alongside a real failure", "Ghost.swift  crap 42.3  func ghost() -> Int { 0 }" in out, out)
    check("beside a failure the tightening command is not offered — it would accept the new debt too", "--write-baseline" not in out, out)
    # restore the fixtures the --config section below still relies on
    lint.pop()
    write(paths["lint"], lint)
    write(core, "package func twisted(_ a: Int) -> Int { a }\n\npackage func thunked(\n    _ a: Int,\n    b: Int = 1\n) -> Int {\n    a + b\n}\n\npackage func braced(\n    _ a: Int,\n    f: () -> Int = { 1 }\n) -> Int {\n    a + f()\n}\n")

    # ---- the same tree, every fact from a fixture quality.json and only --config
    config = os.path.join(tmp, "quality.json")
    crap = {"threshold": 8, "baseline": "crap-baseline.json",
            "sources": ["Acme/Acme", "Acme/AcmeCore/Sources/AcmeCore"],
            "xccov": {"sources": "Acme/Acme", "bundles": "nowhere/*.xcresult"},
            "llvm_cov": {"sources": "Acme/AcmeCore/Sources/AcmeCore", "exports": "nowhere/*.json"}}
    write(config, {"crap": crap})
    def run_config(*args, cwd=nowhere):
        p = subprocess.run([sys.executable, SCRIPT, "--config", config, *args], capture_output=True, text=True, cwd=cwd)
        return p.returncode, p.stdout + p.stderr
    inputs = ("--lint", paths["lint"], "--xccov", paths["xccov"], "--codecov", paths["codecov"])
    code, out = run_config(*inputs)
    check("--config alone: the threshold and coverage roots come from the file and the offenders fail", code == 1 and "beyond the 0 the baseline holds" in out, out)
    check("--config alone: paths are reported relative to the config's directory", "  Acme/Acme/Services/Knot.swift:5  crap 68" in out, out)
    check("--config alone: the package root too", "  Acme/AcmeCore/Sources/AcmeCore/Services/Core.swift:1  crap 21" in out, out)
    code, out = run_config(*inputs, "--write-baseline")
    check("--config alone: the baseline is written where the file says, relative to its directory", code == 0 and os.path.isfile(os.path.join(tmp, "crap-baseline.json")), out)
    code, out = run_config(*inputs)
    check("--config alone: then the check passes with the counts", code == 0 and "7 functions judged, 3 over CRAP 8, all 3 in the baseline" in out, out)
    code, out = run_config(*inputs, "--threshold", "100")
    check("a flag overrides its key", code == 0 and "0 over CRAP 100" in out, out)
    code, out = run_config("--lint", paths["lint"], "--codecov", paths["codecov"])
    check("no bundle matching the configured glob is a FAIL naming the glob", code == 2 and "FAIL: no .xcresult bundle matches nowhere/*.xcresult" in out, out)
    code, out = run_config("--lint", paths["lint"], "--xccov", paths["xccov"])
    check("no export matching the configured glob is a FAIL naming the glob", code == 2 and "FAIL: no llvm-cov export matches nowhere/*.json" in out, out)
    # the config is found by walking up from the working directory
    p = subprocess.run([sys.executable, SCRIPT, *inputs], capture_output=True, text=True, cwd=app)
    check("without --config the nearest quality.json above the working directory is used", p.returncode == 0 and "all 3 in the baseline" in p.stdout, p.stdout + p.stderr)
    # a missing key is named
    del crap["baseline"]; write(config, {"crap": crap})
    code, out = run_config(*inputs)
    check("a missing key fails naming the key", code == 2 and 'FAIL:' in out and '"baseline"' in out, out)
    crap["baseline"] = "crap-baseline.json"; del crap["xccov"]["bundles"]; write(config, {"crap": crap})
    code, out = run_config("--lint", paths["lint"], "--codecov", paths["codecov"])
    check("a missing nested key is named by its path", code == 2 and '"xccov.bundles"' in out, out)
    write(config, {"complexity": {}})
    code, out = run_config(*inputs)
    check("a missing section is named", code == 2 and '"crap" section' in out, out)

    # ---- --bundle: score the named .xcresult directly, never the glob's newest match.
    # A machine with dozens of DerivedData bundles has a glob that matches all of them;
    # --bundle names the one the run just wrote, so nothing is guessed. xcrun is shimmed —
    # it reads a `coverage.json` sitting beside the bundle it is handed, in place of a real
    # .xcresult — so the two bundles below can be told apart by their content alone.
    bundle_root = os.path.join(tmp, "bundle-test")
    bundle_app = os.path.join(bundle_root, "Acme", "Acme")
    bundle_pkg = os.path.join(bundle_root, "Acme", "AcmeCore", "Sources", "AcmeCore")
    bundle_knot = os.path.join(bundle_app, "Services", "Knot.swift")
    write(bundle_knot, "import Foundation\n\nfunc risky(_ a: Int) -> Int { a }\n")
    bundle_lint_path = os.path.join(bundle_root, "lint.json")
    write(bundle_lint_path, [{"file": bundle_knot, "line": 3, "reason": "Function should have complexity 1 or less; currently complexity is 8"}])
    bundle_codecov_path = os.path.join(bundle_root, "codecov.json")
    write(bundle_codecov_path, {"data": []})

    bundles_dir = os.path.join(bundle_root, "nowhere")
    stale_bundle = os.path.join(bundles_dir, "stale.xcresult")
    fresh_bundle = os.path.join(bundles_dir, "fresh.xcresult")
    os.makedirs(stale_bundle); os.makedirs(fresh_bundle)
    # stale — the one --bundle names below — leaves `risky` uncovered: an offender.
    write(os.path.join(stale_bundle, "coverage.json"), {"targets": [{"files": [{"path": bundle_knot, "functions": [
        {"name": "risky", "lineNumber": 3, "executableLines": 10, "coveredLines": 0}]}]}]})
    # fresh — what newest() would pick if the glob were ever consulted — is fully covered:
    # no offender, so a run that reads it instead reports a different result than one that
    # reads stale, and the two cannot be confused for one another by coincidence.
    write(os.path.join(fresh_bundle, "coverage.json"), {"targets": [{"files": [{"path": bundle_knot, "functions": [
        {"name": "risky", "lineNumber": 3, "executableLines": 10, "coveredLines": 10}]}]}]})
    os.utime(stale_bundle, (1_000_000, 1_000_000))
    os.utime(fresh_bundle, (2_000_000, 2_000_000))  # newer mtime — what newest() would pick

    bundle_config = os.path.join(bundle_root, "quality.json")
    write(bundle_config, {"crap": {"threshold": 8, "baseline": "baseline.json",
                                    "sources": ["Acme/Acme"],
                                    "xccov": {"sources": "Acme/Acme", "bundles": "nowhere/*.xcresult"},
                                    "llvm_cov": {"sources": "Acme/AcmeCore/Sources/AcmeCore", "exports": "nowhere/*.json"}}})

    xcrun_shim_dir = os.path.join(bundle_root, "shim")
    os.makedirs(xcrun_shim_dir)
    xcrun_shim = os.path.join(xcrun_shim_dir, "xcrun")
    with open(xcrun_shim, "w") as h:
        h.write("#!/bin/sh\nfor a in \"$@\"; do bundle=\"$a\"; done\n"
                "if [ -f \"$bundle/coverage.json\" ]; then cat \"$bundle/coverage.json\"; exit 0; fi\nexit 1\n")
    os.chmod(xcrun_shim, 0o755)

    def run_bundle(*args):
        env = dict(os.environ); env["PATH"] = xcrun_shim_dir + os.pathsep + env.get("PATH", "")
        p = subprocess.run([sys.executable, SCRIPT, "--config", bundle_config, "--lint", bundle_lint_path,
                             "--codecov", bundle_codecov_path, *args],
                            capture_output=True, text=True, cwd=bundle_root, env=env)
        return p.returncode, p.stdout + p.stderr

    code, out = run_bundle()
    check("without --bundle, the newest bundle matching the glob is read (fully covered)", code == 0, out)
    check("without --bundle, the newer bundle is named — not the one --bundle would name", fresh_bundle in out and stale_bundle not in out, out)

    code, out = run_bundle("--bundle", stale_bundle)
    check("--bundle scores the named .xcresult rather than the glob's newer match", code == 1, out)
    check("--bundle: the offender in the named bundle is reported", "risky" in out, out)
    check("--bundle names the bundle it read from, not the newer one the glob matches", stale_bundle in out and fresh_bundle not in out, out)

    code, out = run_bundle("--bundle", stale_bundle, "--quiet")
    check("--bundle: a FAIL prints the bundle path even under --quiet", stale_bundle in out, out)
    check("--bundle: a FAIL prints the package export path even under --quiet", bundle_codecov_path in out, out)

    code, out = run_bundle("--bundle", stale_bundle, "--write-baseline")
    check("--bundle: --write-baseline accepts the named bundle's offender", code == 0, out)

    code, out = run_bundle("--bundle", stale_bundle)
    check("--bundle: a non-quiet OK line names the bundle and the package export",
          code == 0 and "OK:" in out and stale_bundle in out and bundle_codecov_path in out, out)

    # ---- a hanging swiftlint or xcrun is ended at a ceiling, not left to block the gate forever
    stub_dir = os.path.join(tmp, "stub-bin")
    write(os.path.join(stub_dir, "swiftlint"), "#!/bin/sh\nsleep 30\n")
    os.chmod(os.path.join(stub_dir, "swiftlint"), 0o755)
    old_path = os.environ.get("PATH", "")
    old_swiftlint_timeout, old_xccov_timeout = complexity.SWIFTLINT_TIMEOUT_SECONDS, coverage.XCCOV_TIMEOUT_SECONDS
    os.environ["PATH"] = stub_dir + os.pathsep + old_path
    complexity.SWIFTLINT_TIMEOUT_SECONDS = 1
    try:
        try:
            complexity.swiftlint_violations([app])
            check("a hanging swiftlint raises GateError", False, "lint_complexities returned instead of raising")
        except (complexity.ToolError, coverage.CoverageError) as error:
            check("the error names swiftlint and the ceiling", "swiftlint" in str(error) and "1" in str(error), str(error))
    finally:
        os.environ["PATH"] = old_path
        complexity.SWIFTLINT_TIMEOUT_SECONDS = old_swiftlint_timeout

    write(os.path.join(stub_dir, "xcrun"), "#!/bin/sh\nsleep 30\n")
    os.chmod(os.path.join(stub_dir, "xcrun"), 0o755)
    os.environ["PATH"] = stub_dir + os.pathsep + old_path
    coverage.XCCOV_TIMEOUT_SECONDS = 1
    try:
        try:
            coverage.read_xccov_bundle(stale_bundle)
            check("a hanging xccov raises GateError", False, "read_xccov_bundle returned instead of raising")
        except (complexity.ToolError, coverage.CoverageError) as error:
            check("the error names xccov and the ceiling", "xccov" in str(error) and "1" in str(error), str(error))
    finally:
        os.environ["PATH"] = old_path
        coverage.XCCOV_TIMEOUT_SECONDS = old_xccov_timeout
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print()
if failed: print("test-check-crap: %d case(s) failed." % failed); sys.exit(1)

# --- The stacks SwiftLint does not read: lizard complexity, istanbul coverage, a gate list.
tmp2 = tempfile.mkdtemp(prefix="check-crap-stacks-")
try:
    web = os.path.join(tmp2, "apps", "web", "src", "money.ts")
    write(web, "export function tangled(a: number) {\n  if (a) { return 1 }\n  return 2\n}\nexport function plain() { return 0 }\n")
    rs = os.path.join(tmp2, "apps", "api", "src", "knot.rs")
    write(rs, "fn branchy(a: i32) -> i32 { if a > 0 { 1 } else { 2 } }\n#[cfg(test)]\nmod tests { fn branchy_test() {} }\n")
    csv = os.path.join(tmp2, "lizard.csv")
    write(csv, "\n".join([
        '3,9,20,1,4,"tangled@1-4@%s","%s","tangled","tangled ( a )",1,4' % (web, web),
        '1,1,5,0,1,"plain@5-5@%s","%s","plain","plain ( )",5,5' % (web, web),
        '1,9,20,1,1,"branchy@1-1@%s","%s","branchy","branchy ( a )",1,1' % (rs, rs),
        '1,12,5,0,1,"branchy_test@3-3@%s","%s","branchy_test","branchy_test ( )",3,3' % (rs, rs),
    ]) + "\n")
    istanbul = os.path.join(tmp2, "coverage-final.json")
    write(istanbul, {web: {"path": web,
        "statementMap": {"0": {"start": {"line": 2}, "end": {"line": 2}}, "1": {"start": {"line": 2}, "end": {"line": 2}},
                         "2": {"start": {"line": 3}, "end": {"line": 3}}, "3": {"start": {"line": 5}, "end": {"line": 5}}},
        "fnMap": {"0": {"name": "tangled", "decl": {"start": {"line": 1}}, "loc": {"start": {"line": 1}, "end": {"line": 4}}},
                  "1": {"name": "plain", "decl": {"start": {"line": 5}}, "loc": {"start": {"line": 5}, "end": {"line": 5}}}},
        "s": {"0": 1, "1": 0, "2": 0, "3": 5}, "f": {"0": 1, "1": 5}}})
    cov = coverage.from_istanbul(json.load(open(istanbul)), os.path.join(tmp2, "apps", "web", "src"))
    check("a line inside a function answers with the innermost function's coverage",
          abs(cov.within(web, 3) - 1 / 3) < 1e-9 and cov.within(web, 5) == 1.0 and cov.within(web, 9) is None,
          str((cov.within(web, 3), cov.within(web, 5), cov.within(web, 9))))
    elsewhere = {("/work/" + os.path.relpath(k, tmp2)): dict(v, path="/work/" + os.path.relpath(k, tmp2)) for k, v in json.load(open(istanbul)).items()}
    try:
        coverage.from_istanbul(elsewhere, os.path.join(tmp2, "apps", "web", "src"))
        check("an istanbul export written in another checkout is refused loudly, not read as 0%", False, "no error raised")
    except coverage.CoverageError as error:
        check("an istanbul export written in another checkout is refused loudly, not read as 0%", "none under" in str(error) and "path_map" in str(error), str(error))
    mapped = coverage.from_istanbul(elsewhere, os.path.join(tmp2, "apps", "web", "src"), {"/work/": os.path.join(tmp2, "")})
    check("with a path_map it reads as if written here", mapped == cov, str(mapped))
    x_elsewhere = {"targets": [{"files": [{"path": "/work/App/Thing.swift", "functions": [{"name": "f", "lineNumber": 1, "executableLines": 2, "coveredLines": 1}]}]}]}
    try:
        coverage.from_xccov(x_elsewhere, os.path.join(tmp2, "App"))
        check("an xccov report naming nothing under the root is refused loudly", False, "no error raised")
    except coverage.CoverageError as error:
        check("an xccov report naming nothing under the root is refused loudly", "none under" in str(error), str(error))
    x_mapped = coverage.from_xccov(x_elsewhere, os.path.join(tmp2, "App"), {"/work/": os.path.join(tmp2, "")})
    check("and reads with a path_map", list(x_mapped.values()) == [0.5], str(x_mapped))
    check("istanbul: a function's coverage is the share of its statements that ran", abs(cov[(os.path.realpath(web), 1)] - 1/3) < 1e-9, str(cov))
    check("istanbul: a function whose every statement ran is fully covered", cov[(os.path.realpath(web), 5)] == 1.0, str(cov))
    check("istanbul: files outside the root are not read", len(cov) == 2, str(cov))
    codecov = os.path.join(tmp2, "rust.json")
    write(codecov, {"data": [{"functions": [{"name": "_ZN4knot7branchy", "filenames": [rs],
                                              "regions": [[1, 1, 1, 60, 3, 0, 0, 0], [1, 30, 1, 35, 0, 0, 0, 0]]}]}]})
    config = os.path.join(tmp2, "quality.json")
    write(config, {"crap": [
        {"name": "web", "threshold": 8, "baseline": "crap-web.json",
         "complexity": {"tool": "lizard", "sources": ["apps/web/src"], "languages": ["typescript"]},
         "istanbul": {"sources": "apps/web/src", "exports": "coverage-final.json"}},
        {"name": "rust", "threshold": 8, "baseline": "crap-rust.json",
         "complexity": {"tool": "lizard", "sources": ["apps/api/src"], "languages": ["rust"]},
         "llvm_cov": {"sources": "apps/api/src", "exports": "rust.json"}},
    ]})
    def run2(*args):
        p = subprocess.run([sys.executable, SCRIPT, "--config", config, "--lizard-csv", csv, *args], capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr
    code, out = run2()
    check("a list of gates without --gate fails naming them", code == 2 and "--gate" in out and "web" in out and "rust" in out, out)
    code, out = run2("--gate", "web")
    check("--gate web reads istanbul coverage and lizard complexity", code == 1 and "money.ts:1  crap" in out and "istanbul export" in out, out)
    check("a gate with no xccov key does not look for a bundle", "xcresult" not in out, out)
    check("the plain function is under the gate", "money.ts:5" not in out, out)
    code, out = run2("--gate", "rust")
    check("--gate rust reads llvm-cov coverage: half its regions ran", code == 1 and "knot.rs:1  crap 19 (cc 9, coverage 50%)" in out, out)
    # The export written inside a container names the file under /work; path_map brings it home.
    write(os.path.join(tmp2, "rust-container.json"), {"data": [{"functions": [{"name": "_ZN4knot7branchy", "filenames": ["/work/apps/api/src/knot.rs"],
                                              "regions": [[1, 1, 1, 60, 3, 0, 0, 0], [1, 30, 1, 35, 0, 0, 0, 0]]}]}]})
    cfg = json.load(open(config)); cfg["crap"][1]["llvm_cov"] = {"sources": "apps/api/src", "exports": "rust-container.json", "path_map": {"/work/": "."}}
    write(config, cfg)
    code, out = run2("--gate", "rust")
    check("llvm_cov.path_map joins an export written under another root", code == 1 and "knot.rs:1  crap 19 (cc 9, coverage 50%)" in out, out)
    cfg["crap"][1]["llvm_cov"].pop("path_map"); write(config, cfg)
    code, out = run2("--gate", "rust")
    check("without the map the container path matches nothing — refused loudly, never read as 0%", code == 2 and "none under" in out and "path_map" in out, out)
    cfg["crap"][1]["llvm_cov"]["exports"] = "rust.json"; write(config, cfg)
    check("the inline Rust test function is not judged", "knot.rs:3" not in out, out)
    code, out = run2("--gate", "nowhere")
    check("an unknown gate name fails naming the ones there are", code == 2 and "nowhere" in out and "web" in out, out)
    code, out = run2("--gate", "web", "--write-baseline")
    code, out = run2("--gate", "web")
    check("each gate ratchets on its own baseline", code == 0 and "all 1 in the baseline" in out, out)
finally:
    shutil.rmtree(tmp2, ignore_errors=True)

print("test-check-crap: all cases passed.")

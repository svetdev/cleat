#!/usr/bin/env python3
"""test-check-complexity-lizard — assert the reader, the inline-test skip, the gate and the ratchet
in quality/bin/check-complexity-lizard.py, driven with a saved lizard CSV over a throwaway tree:
a function over the cyclomatic ceiling fails and is named, one over the length ceiling too, a
function inside a Rust `#[cfg(test)]` module is not judged, --write-baseline accepts what is over
the gate, a baselined function passes, a stale entry is noted, a missing key is named. Runs no
lizard, writes nothing outside a temporary directory.
  quality/tests/test-check-complexity-lizard.py
"""
import json, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "check-complexity-lizard.py")
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import lizard_reader  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    print("  %s  %s" % ("ok  " if ok else "FAIL", name) + ("" if ok else "\n          " + detail))
    failed += 0 if ok else 1


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)


def run(config, *args):
    proc = subprocess.run([sys.executable, SCRIPT, "--config", config, *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


tmp = tempfile.mkdtemp(prefix="check-complexity-lizard-")
try:
    src = os.path.join(tmp, "apps", "api", "src")
    knot = os.path.join(src, "knot.rs")
    write(knot, "\n".join([
        "fn simple() {}",                                    # 1
        "fn branchy(a: i32) -> i32 { if a > 0 { 1 } else { 2 } }",  # 2  (cc 9 in the CSV)
        "fn long_one() {",                                   # 3  (61 lines in the CSV)
        "}",
        "#[cfg(test)]",                                      # 5
        "mod tests {",
        "    fn test_branchy() {}",                          # 7  (cc 12 — but a test)
        "}",
        "",
    ]))
    web = os.path.join(tmp, "apps", "web", "src", "thing.ts")
    write(web, "export function tangled(a: number) { return a }\n")  # 1 (cc 10)

    csv_path = os.path.join(tmp, "lizard.csv")
    rows = [
        (1, 1, 5, 0, 1, "simple@1-1@%s" % knot, knot, "simple", "simple ( )", 1, 1),
        (1, 9, 20, 1, 1, "branchy@2-2@%s" % knot, knot, "branchy", "branchy ( a )", 2, 2),
        (2, 1, 5, 0, 61, "long_one@3-4@%s" % knot, knot, "long_one", "long_one ( )", 3, 4),
        (1, 12, 5, 0, 1, "test_branchy@7-7@%s" % knot, knot, "test_branchy", "test_branchy ( )", 7, 7),
        (1, 10, 8, 1, 1, "tangled@1-1@%s" % web, web, "tangled", "tangled ( a )", 1, 1),
    ]
    write(csv_path, "\n".join(",".join('"%s"' % c if isinstance(c, str) else str(c) for c in row) for row in rows) + "\n")

    functions, skipped = lizard_reader.functions_from_csv(open(csv_path).read())
    check("the reader keys functions by realpath and start line", (os.path.realpath(knot), 2) in lizard_reader.complexities(functions))
    check("a function inside a Rust #[cfg(test)] module is skipped, and counted as skipped",
          skipped == 1 and all(f.name != "test_branchy" for f in functions), str([f.name for f in functions]))
    functions_all, _ = lizard_reader.functions_from_csv(open(csv_path).read(), skip_rust_tests=False)
    check("with skip_rust_tests off the test function is judged like any other", any(f.name == "test_branchy" for f in functions_all))

    config = os.path.join(tmp, "quality.json")
    baseline = os.path.join(tmp, "complexity-baseline.json")
    write(config, json.dumps({"complexity_lizard": {
        "sources": ["apps/api/src", "apps/web/src"], "languages": ["rust", "typescript"],
        "ceilings": {"cc": 8, "lines": 60}, "baseline": "complexity-baseline.json"}}))

    code, out = run(config, "--csv", csv_path)
    check("offenders fail the check", code == 1, out)
    check("the function over the cyclomatic ceiling is named with its numbers", "apps/api/src/knot.rs:2  cc 9, 1 lines" in out, out)
    check("the function over the length ceiling is named", "apps/api/src/knot.rs:3  cc 1, 61 lines" in out, out)
    check("the TypeScript offender is named too", "apps/web/src/thing.ts:1  cc 10" in out, out)
    check("the test-module function is not reported", "knot.rs:7" not in out, out)
    check("the simple function is not reported", "knot.rs:1" not in out, out)
    check("the baseline count is in the message", "beyond the 0 the baseline holds" in out, out)

    code, out = run(config, "--csv", csv_path, "--write-baseline")
    check("--write-baseline accepts what is over the gate", code == 0 and "3 function(s) over the gate" in out, out)
    entries = json.load(open(baseline))
    check("the baseline keys by file and declaration text",
          sorted(e["text"] for e in entries) == sorted(["fn branchy(a: i32) -> i32 { if a > 0 { 1 } else { 2 } }", "fn long_one() {", "export function tangled(a: number) { return a }"]), str(entries))

    code, out = run(config, "--csv", csv_path)
    check("with everything baselined the check passes", code == 0, out)
    check("the success line carries the counts, tests skipped included", "4 functions judged (1 inline tests skipped), 3 over the gate, all 3 in the baseline" in out, out)
    code, out = run(config, "--csv", csv_path, "--quiet")
    check("--quiet prints nothing on success", out == "", repr(out))

    entries.append({"file": "apps/api/src/ghost.rs", "text": "fn ghost() {}", "cc": 40, "lines": 3})
    write(baseline, json.dumps(entries))
    code, out = run(config, "--csv", csv_path)
    check("a stale baseline entry does not fail the run", code == 0, out)
    check("the stale entry is named", "ghost.rs  cc 40, 3 lines  fn ghost() {}" in out, out)

    write(config, json.dumps({"complexity_lizard": {"sources": ["apps/api/src"], "languages": ["rust"], "ceilings": {"cc": 8, "lines": 60}}}))
    code, out = run(config, "--csv", csv_path)
    check("a missing key fails naming the key", code == 2 and '"baseline"' in out, out)

    # --- A hanging lizard is ended at a ceiling, not left to block the caller forever.
    stub_dir = os.path.join(tmp, "stub-bin")
    write(os.path.join(stub_dir, "lizard"), "#!/bin/sh\nsleep 30\n")
    os.chmod(os.path.join(stub_dir, "lizard"), 0o755)
    old_path = os.environ["PATH"]
    old_timeout = lizard_reader.LIZARD_TIMEOUT_SECONDS
    os.environ["PATH"] = stub_dir + os.pathsep + old_path
    lizard_reader.LIZARD_TIMEOUT_SECONDS = 1
    try:
        try:
            lizard_reader.run_lizard(["."], ["python"], [])
            check("a hanging lizard raises LizardError", False, "run_lizard returned instead of raising")
        except lizard_reader.LizardError as error:
            check("the error names the ceiling and that the run was ended", "1" in str(error) and "ended" in str(error), str(error))
    finally:
        os.environ["PATH"] = old_path
        lizard_reader.LIZARD_TIMEOUT_SECONDS = old_timeout

    # --- exclude_except: a production file an exclude glob would otherwise drop by name
    # (it matches "*test-*" only because of its filename) is still judged; a file matched
    # by the exclude alone, not named in exclude_except, stays dropped.
    if shutil.which("lizard"):
        ee_src = os.path.join(tmp, "apps", "cli", "src")
        branchy_body = "\n".join(["    if a == 1: return 1", "    elif a == 2: return 2",
                                   "    elif a == 3: return 3", "    elif a == 4: return 4",
                                   "    elif a == 5: return 5", "    elif a == 6: return 6",
                                   "    elif a == 7: return 7", "    elif a == 8: return 8",
                                   "    return 0", ""])
        kept = os.path.join(ee_src, "keep-test-tool.py")
        write(kept, "def kept_fn(a):\n" + branchy_body)
        dropped = os.path.join(ee_src, "other-test-thing.py")
        write(dropped, "def dropped_fn(a):\n" + branchy_body)

        ee_config = os.path.join(tmp, "ee-quality.json")
        write(ee_config, json.dumps({"complexity_lizard": {
            "sources": ["apps/cli/src"], "languages": ["python"], "exclude": ["*test-*"],
            "exclude_except": ["apps/cli/src/keep-test-tool.py"],
            "ceilings": {"cc": 8, "lines": 60}, "baseline": "ee-baseline.json"}}))

        code, out = run(ee_config)
        check("a file matched by exclude and named in exclude_except is judged", "keep-test-tool.py" in out, out)
        check("a file matched by exclude alone is still not judged", "other-test-thing.py" not in out, out)
    else:
        check("lizard is not installed, so exclude_except is not exercised against a real run", True)

    # --- Against this checkout, when it configures the section and lizard is installed.
    repo = os.path.dirname(os.path.dirname(HERE))
    checkout_config = os.path.join(repo, "quality.json")
    if os.path.isfile(checkout_config) and "complexity_lizard" in json.load(open(checkout_config)) and shutil.which("lizard"):
        proc = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, cwd=repo)
        check("this checkout holds its complexity baseline", proc.returncode == 0, proc.stdout + proc.stderr)
    else:
        check("this checkout configures no lizard gate (or has no lizard), so it is not judged", True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("test-check-complexity-lizard: %s" % ("all passed." if failed == 0 else "%d case(s) failed." % failed))
sys.exit(1 if failed else 0)

#!/usr/bin/env python3
"""test-check-escapes — assert the site-keyed ratchet in quality/bin/check-escapes.py.

Over a throwaway tree with a Python file, a TypeScript file and a shell script:
every built-in escape is found and named; --write-baseline accepts every site;
the same tree then passes; a file edited above a site still matches it by line
text; a new site fails naming the file, line and escape, and the failure output
does not print the accept command; the same line added twice raises the site's
count and fails as worse; a removed site is a stale NOTE and a --strict failure;
a project pattern of its own is read; an unknown language and a section naming
nothing are refused. Writes nothing outside a temporary directory.

  python3 quality/tests/test-check-escapes.py
"""
import json, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "check-escapes.py")
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import ratchet

suite = Suite("test-check-escapes"); check = suite.check



def run(config, *args):
    proc = subprocess.run([sys.executable, SCRIPT, "--config", config, *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


tmp = tempfile.mkdtemp(prefix="check-escapes-")
try:
    py = os.path.join(tmp, "src", "thing.py")
    write(py, "import os\n\nx = os.getcwd()  # type: ignore\ny = 1  # noqa\ndef f():\n    try:\n        pass\n    except:\n        pass\n")
    ts = os.path.join(tmp, "web", "thing.ts")
    write(ts, "const a: any = 1;\n// @ts-ignore\nconst b = a!.c;\nit.skip('x', () => {});\nconst c = a as any;\n")
    sh = os.path.join(tmp, "bin", "go.sh")
    write(sh, "#!/bin/sh\nrm -f x || true\n")
    write(os.path.join(tmp, "node_modules", "dep", "index.ts"), "const z: any = 1;\n")
    config = os.path.join(tmp, "quality.json")
    baseline = os.path.join(tmp, "escapes-baseline.json")
    section = {"roots": ["."], "languages": ["python", "typescript", "shell"], "baseline": "escapes-baseline.json"}
    write(config, json.dumps({"escapes": section}))

    code, out = run(config)
    check("with no baseline every site is new and the check fails", code == 1, out)
    for expected in ["src/thing.py:3  type ignore", "src/thing.py:4  noqa", "src/thing.py:8  bare except",
                     "web/thing.ts:1  any", "web/thing.ts:2  ts-ignore", "web/thing.ts:3  non-null assertion",
                     "web/thing.ts:4  skipped test", "web/thing.ts:5  any", "bin/go.sh:2  errors ignored"]:
        check("found: %s" % expected, expected in out, out)
    check("node_modules is not read", "node_modules" not in out, out)
    write(os.path.join(tmp, "src", "thing.test.py"), "y = 1  # type: " + "ignore\n")  # split so this file carries no site
    excluded = os.path.join(tmp, "quality-excluded.json")
    write(excluded, json.dumps({"escapes": {"roots": ["src"], "languages": ["python"], "baseline": "escapes-baseline.json",
                                            "exclude": ["*.test.py"]}}))
    code, out = run(excluded)
    check("a file matching an exclude glob is not read", "thing.test.py" not in out and "thing.py" in out, out)
    os.remove(os.path.join(tmp, "src", "thing.test.py"))
    check("the failure output names the fix and never the accept command",
          "Fix what the escape hides" in out and "--write-baseline" not in out, out)

    code, out = run(config, "--write-baseline")
    check("--write-baseline accepts every site", code == 0 and "9 escape site(s) accepted" in out, out)
    entries, provenance = ratchet.read(baseline)
    check("the baseline keys by file and line text", ("src/thing.py", "x = os.getcwd()  # type: ignore") in {(e["file"], e["text"]) for e in entries}, str(entries))
    check("and records provenance", provenance and provenance["tool"] == "escapes", str(provenance))
    code, out = run(config)
    check("the same tree then passes", code == 0 and "9 escape site(s) in the tree, all 9 in the baseline" in out, out)
    code, out = run(config, "--quiet")
    check("--quiet prints nothing on success", out == "", repr(out))

    write(py, "import os\nimport sys\n\n\nx = os.getcwd()  # type: ignore\ny = 1  # noqa\ndef f():\n    try:\n        pass\n    except:\n        pass\n")
    code, out = run(config)
    check("a site shifted by edits above it still matches", code == 0, out)

    write(py, "import os\nimport sys\n\n\nx = os.getcwd()  # type: ignore\ny = 1  # noqa\nz = 2  # noqa: E501\ndef f():\n    try:\n        pass\n    except:\n        pass\n")
    code, out = run(config)
    check("a new site fails", code == 1, out)
    check("and is named with its file, line and escape", "src/thing.py:7  noqa  z = 2  # noqa: E501" in out, out)
    check("beyond the count the baseline holds", "beyond the 9 the baseline holds" in out, out)

    write(py, "import os\n\nx = os.getcwd()  # type: ignore\ny = 1  # noqa\ndef f():\n    try:\n        pass\n    except:\n        pass\ny = 1  # noqa\n")
    code, out = run(config)
    check("the same line carried twice raises the site's count and fails as worse", code == 1 and "got worse" in out and "noqa x2, was noqa" in out, out)

    write(py, "import os\n\nx = os.getcwd()  # type: ignore\ndef f():\n    try:\n        pass\n    except:\n        pass\n")
    code, out = run(config)
    check("a removed site passes with a stale NOTE and the tightening command", code == 0 and "matched nothing" in out and "--write-baseline" in out, out)
    code, out = run(config, "--strict")
    check("and fails under --strict", code == 1 and "looser than the code" in out, out)

    write(config, json.dumps({"escapes": dict(section, patterns={"todo bang": r"TODO!"})}))
    write(ts, "const a: any = 1;\n// @ts-ignore\nconst b = a!.c;\nit.skip('x', () => {});\nconst c = a as any;\n// TODO! later\n")
    code, out = run(config)
    check("a project pattern of its own is read and named", code == 1 and "web/thing.ts:6  todo bang" in out, out)

    write(config, json.dumps({"escapes": dict(section, languages=["cobol"])}))
    code, out = run(config)
    check("an unknown language is refused naming the known ones", code == 2 and "cobol" in out and "python" in out, out)
    write(config, json.dumps({"escapes": {"roots": ["."], "baseline": "b.json"}}))
    code, out = run(config)
    check("a section naming nothing to look for is refused", code == 2 and "nothing to look for" in out, out)

    rs = os.path.join(tmp, "src", "lib.rs")
    write(rs, "pub fn read() -> i32 { let v: Result<i32, ()> = Ok(1); v.unwrap() }\n\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn t() { let x: Result<i32, ()> = Ok(1); x.unwrap(); x.expect(\"no\"); }\n}\n\npub fn after() -> i32 { let v: Result<i32, ()> = Ok(1); v.expect(\"appended below the tests\") }\n")
    write(config, json.dumps({"escapes": {"roots": ["src"], "languages": ["rust"], "baseline": "rs-baseline.json"}}))
    code, out = run(config)
    check("escapes inside an inline #[cfg(test)] module are not production sites", code == 1 and "2 new escape site(s)" in out and "src/lib.rs:1  unwrap" in out and ":6" not in out, out)
    check("#4: production code appended after the test module's closing brace is judged", "src/lib.rs:9  expect" in out, out)
    code, out = run(config, "--write-baseline"); code, out = run(config)
    check("and the success line says how many were skipped as tests", code == 0 and "(2 in inline Rust tests skipped)" in out, out)
    write(config, json.dumps({"escapes": {"roots": ["src"], "languages": ["rust"], "skip_rust_tests": False, "baseline": "rs-baseline.json"}}))
    code, out = run(config)
    check("skip_rust_tests: false counts them", code == 1 and "src/lib.rs:6" in out, out)
    write(config, json.dumps({"escapes": section}))

    code, out = run(config, "--list-languages")
    check("--list-languages prints every built-in set", code == 0 and "swift" in out and "force unwrap" in out, out)

    repo = os.path.dirname(os.path.dirname(HERE))
    real = os.path.join(repo, "quality.json")
    if os.path.isfile(real) and "escapes" in json.load(open(real)):
        proc = subprocess.run([sys.executable, SCRIPT, "--strict"], capture_output=True, text=True, cwd=repo)
        check("this checkout holds its escapes baseline", proc.returncode == 0, proc.stdout + proc.stderr)
    else:
        check("this checkout configures no escapes gate, so it is not judged", True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

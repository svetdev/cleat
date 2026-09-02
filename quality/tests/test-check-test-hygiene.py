#!/usr/bin/env python3
"""test-check-test-hygiene — assert the ratchet in quality/bin/check-test-hygiene.py.

Driven through `--config` over a throwaway quality.json this writes, with its own
small habits table, over throwaway test trees: a tree under every ceiling passes
and the success line carries the counts; one site over a zero ceiling fails and
names the habit, its count, the ceiling and the spelling to use; a site in a
comment or under a skipped directory is not counted; `--tests` overrides the
config's roots; a config missing the section fails naming it; and this checkout,
under the quality.json at the repository root, passes, so a new sleep fails the
preflight here — and every habit's `use` that names a path names one that
actually exists, so a helper that moves without its ratchet spelling following
fails here too. Starts no build, reads no process list, writes nothing outside a
temporary directory.

  quality/tests/test-check-test-hygiene.py
"""
import json, os, re, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "check-test-hygiene.py")
REPO = os.path.dirname(os.path.dirname(HERE))
HABITS = {
    "fixed sleeps": {"pattern": r"Task\.sleep\(|\busleep\(|Thread\.sleep\(", "ceiling": 2, "use": "eventually { … }"},
    "loopback port probes": {"pattern": r"UInt16\.random\(", "ceiling": 0, "use": "ControlServerHarness.start"},
}
failed = 0
def check(name, ok, detail=""):
    global failed
    print("  %s  %s" % ("ok  " if ok else "FAIL", name) + ("" if ok else "\n          " + detail))
    failed += 0 if ok else 1
def write(root, rel, text):
    p = os.path.join(root, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as h: h.write(text)
def write_config(root, section):
    p = os.path.join(root, "quality.json")
    with open(p, "w") as h: json.dump({"project": "Fixture", "hygiene": section} if section is not None else {"project": "Fixture"}, h)
    return p
def run(*args, cwd=None):
    p = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, cwd=cwd)
    return p.returncode, p.stdout + p.stderr
tmp = tempfile.mkdtemp(prefix="check-test-hygiene-")
try:
    config = write_config(tmp, {"roots": ["clean"], "skip_dirs": ["Support"], "habits": HABITS})
    clean = os.path.join(tmp, "clean")
    write(clean, "FooTests.swift", "import XCTest\nfinal class FooTests: XCTestCase {\n    func testA() { /* Task.sleep in a comment */ }\n    // UInt16.random( in a comment\n}\n")
    write(clean, "Support/Helper.swift", "let probe = UInt16.random(in: 1...2)\n")
    code, out = run("--config", config)
    check("a clean tree under the config's roots passes", code == 0, out)
    check("comments and a skipped directory are not counted", "loopback port probes 0/0" in out and "fixed sleeps 0/2" in out, out)
    check("the success line carries only the config's habits", "hand-built temp directories" not in out, out)
    code, out = run("--config", config, "--quiet")
    check("--quiet prints nothing on success", code == 0 and out == "", repr(out))
    dirty = os.path.join(tmp, "dirty")
    write(dirty, "BarTests.swift", "import XCTest\nfinal class BarTests: XCTestCase {\n    func testA() async {\n        let port = UInt16.random(in: 49_152...65_000)\n        _ = port\n    }\n}\n")
    code, out = run("--config", config, "--tests", dirty)
    check("--tests overrides the config's roots, and a site over a zero ceiling fails", code == 1, out)
    check("and names the habit, the count, the ceiling and the spelling", "loopback port probes: 1, ceiling 0 — use ControlServerHarness.start" in out, out)
    check("and lists the site's path:line relative to the repository root", "    dirty/BarTests.swift:4" in out, out)
    check("and points at the config to raise the ceiling in", config in out, out)
    mixed = os.path.join(tmp, "mixed")
    write(mixed, "MixedTests.swift", "import XCTest\nfinal class MixedTests: XCTestCase {\n    func testA() async throws {\n        try await Task.sleep(for: .seconds(1))\n        let port = UInt16.random(in: 1...2)\n        _ = port\n    }\n}\n")
    code, out = run("--config", config, "--tests", mixed)
    check("a habit at or under its ceiling contributes no site lines", "fixed sleeps" not in out, out)
    check("an over-ceiling habit lists its site", "    mixed/MixedTests.swift:5" in out, out)
    blockcomment = os.path.join(tmp, "blockcomment")
    write(blockcomment, "Big.swift", "import XCTest\n/*\nmulti\nline\ncomment\n*/\nfinal class BigTests: XCTestCase {\n    func testA() {\n        let port = UInt16.random(in: 1...2)\n        _ = port\n    }\n}\n")
    code, out = run("--config", config, "--tests", blockcomment)
    check("a site after a multi-line block comment reports its real line number", "    blockcomment/Big.swift:9" in out, out)
    many = os.path.join(tmp, "many")
    write(many, "ManyTests.swift", "\n".join("let p%d = UInt16.random(in: 1...2)" % i for i in range(25)) + "\n")
    code, out = run("--config", config, "--tests", many)
    check("more than 20 sites print only the first 20", out.count("many/ManyTests.swift:") == 20, out)
    check("and a tail names how many more", "… and 5 more" in out, out)
    sleepy = os.path.join(tmp, "sleepy")
    write(sleepy, "BazTests.swift", "func testA() async throws {\n    try await Task.sleep(for: .seconds(1))\n    try await Task.sleep(for: .seconds(1))\n}\n")
    code, out = run("--config", config, "--tests", sleepy)
    check("a count at its ceiling passes", code == 0 and "fixed sleeps 2/2" in out, out)
    code, out = run("--config", config, cwd=dirty)
    check("--config wins over the nearest quality.json above the working directory", code == 0, out)
    missing = os.path.join(tmp, "missing"); os.makedirs(missing)
    code, out = run("--config", write_config(missing, None))
    check("a config without a hygiene section fails naming it", code != 0 and '"hygiene"' in out, out)
    real_config = json.load(open(os.path.join(REPO, "quality.json")))
    if "hygiene" in real_config:
        code, out = run("--config", os.path.join(REPO, "quality.json"))
        check("this checkout holds its ceilings", code == 0, out)
        habits = real_config["hygiene"]["habits"]
        check("and the success line carries every count",
              all(re.search(r"%s \d+/\d+" % re.escape(name), out) for name in habits), out)
    else:
        check("this checkout configures no hygiene, so it is not judged", True, "")
    companion = os.path.join(tmp, "companion")
    companion_habits = {"fixed sleeps": {**HABITS["fixed sleeps"], "ceiling": 0}}
    companion_config = write_config(tmp, {"roots": ["companion/AcmeMobile/Tests"], "skip_dirs": [], "habits": companion_habits})
    write(companion, "AcmeMobile/Tests/FooTests.swift",
          "import XCTest\nfinal class FooTests: XCTestCase {\n    func testA() {\n        Thread.sleep(forTimeInterval: 1)\n    }\n}\n")
    code, out = run("--config", companion_config)
    check("a fixed sleep in a companion-shaped test tree is counted",
          code == 1 and "fixed sleeps: 1, ceiling 0" in out and "companion/AcmeMobile/Tests/FooTests.swift:4" in out, out)
    def use_path(use):
        # a path is one token: no spaces, a slash inside it, letters, dots, dashes — not a
        # sentence that happens to contain a slash ("findBy… / waitFor")
        tokens = [tok.strip("()`,.;") for tok in use.split()]
        paths = [tok for tok in tokens if re.fullmatch(r"[\w.\-]+(?:/[\w.\-]+)+", tok)]
        return paths[0] if paths else None
    dead_ends = [(name, use_path(habit["use"])) for name, habit in habits.items()
                 if use_path(habit["use"]) and not os.path.exists(os.path.join(REPO, use_path(habit["use"])))]
    check("and every use naming a path names one on disk under the repository root", not dead_ends, str(dead_ends))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print()
if failed: print("test-check-test-hygiene: %d case(s) failed." % failed); sys.exit(1)
print("test-check-test-hygiene: all cases passed.")

#!/usr/bin/env python3
"""test-check-duplication — assert quality/bin/check-duplication.py and the
extractors under it.

Over a throwaway git repository: the built-in finder reports a block copied
between two files with both locations and its length, not a block shorter than
min_lines, not a file copying itself at an overlap; the density baseline is
written and held; a committed tree with the clone accepted passes; a new copy
in the working tree fails the changed-lines judgment even though the density
baseline is untouched, and names both copies; --repo-only skips it; density
rising past the baseline fails as worse; a jscpd report is read instead of the
finder; changed lines come from the diff against the base plus untracked files.
Writes nothing outside a temporary directory.

  python3 quality/tests/test-check-duplication.py
"""
import json, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "check-duplication.py")
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
from extractors import patterns, changed, duplication

suite = Suite("test-check-duplication"); check = suite.check



def git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)


def run(config, *args):
    proc = subprocess.run([sys.executable, SCRIPT, "--config", config, *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


BLOCK = "\n".join("    total = total + item.price * item.count  # step %d" % i for i in range(8)) + "\n"

tmp = tempfile.mkdtemp(prefix="check-duplication-")
try:
    repo = os.path.join(tmp, "repo")
    a = os.path.join(repo, "src", "a.py")
    b = os.path.join(repo, "src", "b.py")
    c = os.path.join(repo, "src", "c.py")
    write(a, "def total_a(items):\n    total = 0\n" + BLOCK + "    return total\n")
    write(b, "def total_b(items):\n    total = 0\n" + BLOCK + "    return total\n")
    write(c, "def other():\n    x = 1\n    y = 2\n    z = 3\n    return x + y + z\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")

    clones = duplication.find([a, b, c], repo, min_lines=6)
    check("the finder reports the copied block once, with both locations", len(clones) == 1 and sorted(l[0] for l in clones[0].locations) == ["src/a.py", "src/b.py"], str([(x.locations, x.lines) for x in clones]))
    check("with its length in significant lines", clones[0].lines == 10, str(clones[0].lines))
    check("and the line range of each copy", clones[0].locations[0][1:] == (2, 11), str(clones[0].locations))
    check("a block shorter than min_lines is not a clone", duplication.find([a, b, c], repo, min_lines=12) == [])
    check("an exclude glob drops a file from the walk by name or by path",
          patterns.excluded("/x/src/a.test.py", ["*.test.py"]) and patterns.excluded("/x/fixtures/a.py", ["*/fixtures/*"])
          and not patterns.excluded("/x/src/a.py", ["*.test.py", "*/fixtures/*"]))
    dup, total = duplication.density(clones, [a, b, c], repo)
    check("density counts the lines inside clones against every significant line", (dup, total) == (20, 27), str((dup, total)))

    config = os.path.join(repo, "quality.json")
    write(config, json.dumps({"duplication": {"roots": ["src"], "languages": ["python"], "baseline": "duplication-baseline.json"}}))
    code, out = run(config, "--repo-only")
    check("with no baseline the density is new and fails", code == 1 and "no baseline yet" in out and "74.07% duplicated (20 of 27 lines)" in out, out)
    code, out = run(config, "--write-baseline")
    check("--write-baseline accepts today's density", code == 0 and "74.07% of 27 significant lines duplicated (1 clone pairs)" in out, out)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "baseline")

    code, out = run(config)
    check("the committed tree passes: within the baseline, nothing changed", code == 0 and "within the baseline; none overlap the 0 changed line(s)" in out, out)
    code, out = run(config, "--quiet")
    check("--quiet prints nothing on success", out == "", repr(out))

    write(c, "def other():\n    x = 1\n    y = 2\n    z = 3\n    return x + y + z\n\n\ndef total_c(items):\n    total = 0\n" + BLOCK + "    return total\n")
    code, out = run(config)
    check("a new copy in the working tree fails the changed-lines judgment", code == 1 and "overlap the" in out and "the block has a twin" in out, out)
    check("naming the copies", "src/c.py:9-18" in out and ("src/a.py:2-11" in out or "src/b.py:2-11" in out), out)
    check("and the density is reported as worse too", "got worse" in out and "was 74.07%" in out, out)
    check("the failure output never prints the accept command", "--write-baseline" not in out, out)
    code, out = run(config, "--repo-only")
    check("--repo-only skips the changed-lines judgment but still fails on density", code == 1 and "overlap" not in out and "got worse" in out, out)

    lines = changed.changed_lines(repo, changed.base_ref(repo))
    check("changed lines come from the diff against the base", lines.get("src/c.py") == set(range(6, 19)), str(lines))
    write(os.path.join(repo, "src", "new.py"), "x = 1\ny = 2\n")
    lines = changed.changed_lines(repo, "HEAD")
    check("an untracked file counts as changed in full", lines.get("src/new.py") == {1, 2, 3}, str(lines))
    os.remove(os.path.join(repo, "src", "new.py"))

    git(repo, "checkout", "-q", "--", "src/c.py")
    report = os.path.join(repo, "jscpd.json")
    write(report, json.dumps({"duplicates": [{"lines": 7, "firstFile": {"name": "src/a.py", "start": 3, "end": 9},
                                               "secondFile": {"name": os.path.join(repo, "src", "b.py"), "start": 3, "end": 9}}]}))
    write(config, json.dumps({"duplication": {"roots": ["src"], "languages": ["python"], "baseline": "duplication-baseline.json",
                                              "report": {"jscpd": "jscpd.json"}}}))
    code, out = run(config, "--repo-only")
    check("a jscpd report is read instead of the finder, absolute paths made relative", code == 0 and "14 significant lines" not in out and "(1 clone pairs)" in out, out)
    entries, prov = json.load(open(os.path.join(repo, "duplication-baseline.json")))["entries"], None
    check("the baseline holds the one density measurement", len(entries) == 1 and entries[0]["percent"] == 74.07, str(entries))

    rs_a = os.path.join(repo, "rs", "a.rs"); rs_b = os.path.join(repo, "rs", "b.rs")
    test_block = "#[cfg(test)]\nmod tests {\n" + "\n".join("    fn t%d() { assert_eq!(compute(%d), %d); }" % (i, i, i) for i in range(8)) + "\n}\n"
    write(rs_a, "pub fn a() -> i32 { 1 }\n\n" + test_block)
    write(rs_b, "pub fn b() -> i32 { 2 }\n\n" + test_block)
    after = "\n" + "\n".join("pub fn after_%d(x: i32) -> i32 { x * %d + 1 }" % (i, i) for i in range(8)) + "\n"
    write(os.path.join(repo, "rs", "c.rs"), "pub fn c() -> i32 { 3 }\n\n" + test_block + after)
    write(os.path.join(repo, "rs", "d.rs"), "pub fn d() -> i32 { 4 }\n\n" + test_block + after)
    tail = duplication.find([os.path.join(repo, "rs", "c.rs"), os.path.join(repo, "rs", "d.rs")], repo)
    check("#4: a block copied below the test module's closing brace is a production clone", len(tail) == 1 and tail[0].locations[0][1] > 11, str([(c.locations, c.lines) for c in tail]))
    check("clones inside inline #[cfg(test)] modules are not production clones", duplication.find([rs_a, rs_b], repo) == [], str([(c.locations, c.lines) for c in duplication.find([rs_a, rs_b], repo)]))
    check("but count when skip_rust_tests is off", len(duplication.find([rs_a, rs_b], repo, skip_rust_tests=False)) == 1)
    check("and density counts production lines only", duplication.density([], [rs_a, rs_b], repo) == (0, 2), str(duplication.density([], [rs_a, rs_b], repo)))

    write(config, json.dumps({"duplication": {"roots": ["src"], "baseline": "b.json"}}))
    code, out = run(config)
    check("a section naming no languages or suffixes is refused", code == 2 and "nothing to read" in out, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

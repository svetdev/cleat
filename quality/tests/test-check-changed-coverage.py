#!/usr/bin/env python3
"""test-check-changed-coverage — assert extractors/coverage.py, the changed-line
coverage gate, and the CRAP gate reading LCOV and Cobertura.

Over a throwaway git repository with a committed Python file: an LCOV report
and a Cobertura report read into the same shape; a function's coverage is the
share of its executable lines that ran, its range ending where the next
function starts; a change under min_lines is not judged; a change whose lines
mostly ran passes naming the share; one whose lines did not fails naming the
file and the line ranges; a changed file the report does not know is counted
but not judged; a missing report is refused; and check-crap.py scores a
function from LCOV and from Cobertura through --lcov / --cobertura. Writes
nothing outside a temporary directory.

  python3 quality/tests/test-check-changed-coverage.py
"""
import json, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write
BIN = os.path.join(os.path.dirname(HERE), "bin")
SCRIPT = os.path.join(BIN, "check-changed-coverage.py")
CRAP = os.path.join(BIN, "check-crap.py")
sys.path.insert(0, BIN)
from extractors import coverage

suite = Suite("test-check-changed-coverage"); check = suite.check



def git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)


def run(script, *args):
    proc = subprocess.run([sys.executable, script, *args], capture_output=True, text=True, cwd=tmp)
    return proc.returncode, proc.stdout + proc.stderr


tmp = tempfile.mkdtemp(prefix="changed-coverage-")
try:
    repo = os.path.join(tmp, "repo")
    src = os.path.join(repo, "src", "calc.py")
    original = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
    write(src, original)
    git(repo, "init", "-q", "-b", "main"); git(repo, "config", "user.email", "t@example.com"); git(repo, "config", "user.name", "T")
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "base")

    # the change: a branchy function appended, lines 9-20; only some of it runs
    branchy = "\n\ndef branchy(a):\n    if a == 1:\n        return 1\n    if a == 2:\n        return 2\n    if a == 3:\n        return 3\n    if a == 4:\n        return 4\n    return 0\n"
    write(src, original + branchy)
    lcov = "TN:\nSF:src/calc.py\nFN:1,add\nFN:5,sub\nFN:9,branchy\nFNDA:3,add\nFNDA:0,sub\nFNDA:1,branchy\n" + \
        "".join("DA:%d,%d\n" % (n, h) for n, h in [(1, 1), (2, 3), (5, 1), (6, 0), (9, 1), (10, 1), (11, 1), (12, 1), (13, 0), (14, 1), (15, 0), (16, 1), (17, 0), (18, 0)]) + "end_of_record\n"
    lcov_path = os.path.join(repo, "coverage", "lcov.info")
    write(lcov_path, lcov)
    report = coverage.read(lcov_path, repo)
    real = os.path.realpath(src)
    check("LCOV reads files by absolute path with their lines and functions", real in report and report[real]["lines"][2] == 3 and len(report[real]["functions"]) == 3, str(report))
    fc = coverage.function_coverage(report, os.path.join(repo, "src"))
    check("a function's coverage is the share of its executable lines that ran, up to the next function", abs(fc[(real, 1)] - 1.0) < 1e-9 and abs(fc[(real, 5)] - 0.5) < 1e-9 and abs(fc[(real, 9)] - 0.6) < 1e-9, str(fc))

    cobertura = ('<?xml version="1.0" ?><coverage><sources><source>%s</source></sources><packages><package name="src"><classes>'
                 '<class name="calc" filename="src/calc.py"><methods>'
                 '<method name="add"><lines><line number="1" hits="1"/><line number="2" hits="3"/></lines></method>'
                 '<method name="branchy"><lines>%s</lines></method></methods>'
                 '<lines><line number="1" hits="1"/><line number="2" hits="3"/><line number="5" hits="1"/><line number="6" hits="0"/>%s</lines>'
                 '</class></classes></package></packages></coverage>') % (
        repo, "".join('<line number="%d" hits="%d"/>' % (n, h) for n, h in [(9, 1), (10, 1), (11, 1), (12, 1), (13, 0), (14, 1), (15, 0), (16, 1), (17, 0), (18, 0)]),
        "".join('<line number="%d" hits="%d"/>' % (n, h) for n, h in [(9, 1), (10, 1), (11, 1), (12, 1), (13, 0), (14, 1), (15, 0), (16, 1), (17, 0), (18, 0)]))
    cob_path = os.path.join(repo, "coverage", "coverage.xml")
    write(cob_path, cobertura)
    cob = coverage.read(cob_path, repo)
    check("Cobertura reads the same shape, filenames resolved against <source>", real in cob and cob[real]["lines"][6] == 0, str(cob))
    fcc = coverage.function_coverage(cob, os.path.join(repo, "src"))
    check("Cobertura methods give per-function coverage from their own lines", abs(fcc[(real, 9)] - 0.6) < 1e-9 and abs(fcc[(real, 1)] - 1.0) < 1e-9, str(fcc))

    config = os.path.join(repo, "quality.json")
    write(config, json.dumps({"changed_coverage": {"report": "coverage/lcov.info", "minimum": 0.8, "min_lines": 5}}))
    code, out = run(SCRIPT, "--config", config, "--min-lines", "50")
    check("a change under min_lines is not judged", code == 0 and "under the 50 it takes to judge" in out, out)
    code, out = run(SCRIPT, "--config", config)
    check("a change whose lines mostly did not run fails, naming the share and the minimum", code == 1 and "6 of 10 changed executable line(s) ran (60%), under the 80% minimum" in out, out)
    check("and the uncovered ranges", "src/calc.py: lines 13, 15, 17-18" in out, out)
    code, out = run(SCRIPT, "--config", config, "--minimum", "0.5")
    check("a lower minimum passes naming the share", code == 0 and "(60%, minimum 50%)" in out, out)
    code, out = run(SCRIPT, "--config", config, "--minimum", "0.5", "--quiet")
    check("--quiet prints nothing on success", out == "", repr(out))
    write(os.path.join(repo, "src", "unknown.py"), "x = 1\ny = 2\n")
    code, out = run(SCRIPT, "--config", config)
    check("a changed file the report does not know is counted but not judged", code == 1 and "1 changed file(s) the report does not know" in out, out)
    os.remove(os.path.join(repo, "src", "unknown.py"))
    code, out = run(SCRIPT, "--config", config, "--report", cob_path)
    check("a Cobertura report judges the same change the same way", code == 1 and "6 of 10 changed executable line(s) ran (60%)" in out, out)
    code, out = run(SCRIPT, "--config", config, "--report", os.path.join(repo, "coverage", "missing.info"))
    check("a missing report is refused", code == 2 and "no coverage report" in out, out)
    write(config, json.dumps({"project": "x"}))
    code, out = run(SCRIPT, "--config", config)
    check("a config without the report key is refused naming it", code == 2 and '"report"' in out, out)

    # ---- check-crap.py through --lcov and --cobertura: branchy is cc 5 at 60% → crap 5*5*0.4^3+5 = 6.6; sub cc 1 at 50%
    csv_path = os.path.join(tmp, "lizard.csv")
    rows = [(2, 1, 5, 2, 2, "add@1-2@%s" % src, src, "add", "add ( a , b )", 1, 2),
            (2, 1, 5, 2, 2, "sub@5-6@%s" % src, src, "sub", "sub ( a , b )", 5, 6),
            (10, 5, 30, 1, 10, "branchy@9-18@%s" % src, src, "branchy", "branchy ( a )", 9, 18)]
    write(csv_path, "\n".join(",".join('"%s"' % c if isinstance(c, str) else str(c) for c in row) for row in rows) + "\n")
    baseline = os.path.join(tmp, "crap-baseline.json")
    for flag, path in (("--lcov", lcov_path), ("--cobertura", cob_path)):
        code, out = run(CRAP, "--lizard-csv", csv_path, flag, path, "--report-sources", os.path.join(repo, "src"),
                        "--baseline", baseline, "--repo", repo, "--threshold", "6")
        check("check-crap.py scores from %s: the branchy function is over CRAP 6" % flag[2:], code == 1 and "src/calc.py:9  crap 7 (cc 5, coverage 60%)" in out, out)
        check("and names the report it read", "%s %s" % (flag[2:], path) in out, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

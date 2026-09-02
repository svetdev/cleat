#!/usr/bin/env python3
"""test-check-sarif — assert quality/bin/check-sarif.py over a hand-made SARIF file.

Results are read across runs with their rule, message, file and line, a
uriBaseId resolved and an absolute file: URI made relative, two results at one
site counted as one; --write-baseline accepts them; the same report passes; a
result that moved lines still matches by file, rule and message; a new result
fails naming it; a count that came down is an improved NOTE and a --strict
failure; the config's sarif list is selected by
--gate and a wrong name is refused. Writes nothing outside a temporary directory.

  python3 quality/tests/test-check-sarif.py
"""
import json, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "check-sarif.py")
suite = Suite("test-check-sarif"); check = suite.check


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(data if isinstance(data, str) else json.dumps(data))


def run(*args, cwd=None):
    proc = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, cwd=cwd)
    return proc.returncode, proc.stdout + proc.stderr


def result(uri, line, rule, message, base=None):
    loc = {"physicalLocation": {"artifactLocation": {"uri": uri}, "region": {"startLine": line}}}
    if base:
        loc["physicalLocation"]["artifactLocation"]["uriBaseId"] = base
    return {"ruleId": rule, "message": {"text": message}, "locations": [loc]}


tmp = tempfile.mkdtemp(prefix="check-sarif-")
try:
    repo = os.path.join(tmp, "repo"); os.makedirs(repo)
    report = {"runs": [
        {"originalUriBaseIds": {"SRC": {"uri": "file://%s/" % repo}},
         "results": [result("src/a.py", 10, "no-eval", "eval is dangerous", base="SRC"),
                     result("file://%s/src/b.py" % repo, 3, "unused", "x is unused")]},
        {"results": [result("src/a.py", 20, "no-eval", "eval is dangerous")]}]}
    report_path = os.path.join(repo, "scan.sarif"); write(report_path, report)
    baseline = os.path.join(repo, "sarif-baseline.json")
    flags = ["--report", report_path, "--baseline", baseline, "--repo", repo]

    code, out = run(*flags, cwd=tmp)
    check("with no baseline every result is new and fails", code == 1 and "2 new sarif result(s)" in out, out)
    check("two results with one file, rule and message are one site with a count", "src/a.py:10  x2  no-eval" in out, out)
    check("a uriBaseId is resolved and an absolute file: URI made relative", "src/a.py:10  " in out and "src/b.py:3  " in out, out)
    check("results are keyed by file, rule and message", "no-eval: eval is dangerous" in out and "unused: x is unused" in out, out)
    code, out = run(*flags, "--write-baseline", cwd=tmp)
    check("--write-baseline accepts them", code == 0 and "2 result site(s) accepted" in out, out)
    code, out = run(*flags, cwd=tmp)
    check("the same report passes", code == 0 and "all 2 in the baseline" in out, out)

    report["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]["startLine"] = 14
    write(report_path, report)
    code, out = run(*flags, cwd=tmp)
    check("a result that moved lines still matches", code == 0, out)
    report["runs"][1]["results"].append(result("src/c.py", 1, "no-eval", "eval is dangerous"))
    write(report_path, report)
    code, out = run(*flags, cwd=tmp)
    check("a new result fails naming it", code == 1 and "src/c.py:1  " in out and "no-eval: eval is dangerous" in out, out)
    check("and never prints the accept command", "--write-baseline" not in out, out)
    report["runs"][1]["results"] = []
    write(report_path, report)
    code, out = run(*flags, cwd=tmp)
    check("one of a site's results gone is an improved NOTE — the count came down", code == 0 and "improved" in out and "baseline says x2" in out, out)
    code, out = run(*flags, "--strict", cwd=tmp)
    check("and a --strict failure", code == 1, out)

    config = os.path.join(repo, "quality.json")
    write(config, {"sarif": [{"name": "scan", "report": "scan.sarif", "baseline": "sarif-baseline.json"},
                             {"name": "other", "report": "other-*.sarif", "baseline": "other-baseline.json"}]})
    code, out = run("--config", config, "--gate", "scan", "--write-baseline")
    code, out = run("--config", config, "--gate", "scan")
    check("the config's list is selected by --gate", code == 0 and "all 2 in the baseline" in out and "scan result" in out, out)
    code, out = run("--config", config, "--gate", "nope")
    check("an unknown gate name is refused naming the known ones", code == 2 and "scan, other" in out, out)
    code, out = run("--config", config, "--gate", "other")
    check("a gate whose report is missing is refused", code == 2 and "no SARIF report matches" in out, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

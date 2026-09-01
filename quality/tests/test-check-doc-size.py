#!/usr/bin/env python3
"""test-check-doc-size — assert the ceilings in quality/bin/check-doc-size.py.

A file under the ceiling passes and the success line carries both numbers; a
file over it fails and names the count and the ceiling; --quiet prints nothing
on success; a missing file is refused; a config listing two documents names
the one over its ceiling and not the other; and this checkout's documents are
under their ceilings, so the preflight stops here the day one is not. Writes
nothing outside a temporary directory, starts no build.

  quality/tests/test-check-doc-size.py
"""
import json, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "check-doc-size.py")
REPO = os.path.dirname(os.path.dirname(HERE))
failed = 0
def check(name, ok, detail=""):
    global failed
    print("  %s  %s" % ("ok  " if ok else "FAIL", name) + ("" if ok else "\n          " + detail)); failed += 0 if ok else 1
def run(*args, cwd=None):
    p = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, cwd=cwd); return p.returncode, p.stdout + p.stderr
tmp = tempfile.mkdtemp(prefix="check-doc-size-")
try:
    small = os.path.join(tmp, "small.md"); open(small, "w").write("one two three four five\n")
    code, out = run("--file", small, "--ceiling", "10")
    check("a file under the ceiling passes", code == 0, out)
    check("and the success line carries both numbers", "is 5 words, ceiling 10" in out, out)
    code, out = run("--file", small, "--ceiling", "10", "--quiet")
    check("--quiet prints nothing on success", code == 0 and out == "", repr(out))
    code, out = run("--file", small, "--ceiling", "4")
    check("a file over the ceiling fails", code == 1, out)
    check("and names the count and the ceiling", "is 5 words, over its ceiling of 4" in out, out)
    code, out = run("--file", os.path.join(tmp, "missing.md"))
    check("a missing file is refused", code == 2, out)

    margin = os.path.join(tmp, "margin.md")
    open(margin, "w").write((" ".join(["word"] * 99)) + "\n")
    code, out = run("--file", margin, "--ceiling", "100", "--quiet")
    check("a file just inside the margin warns even under --quiet", code == 0, out)
    check("naming the words left", ("WARN: %s is 99 words, 1 from its ceiling of 100." % margin) in out, out)
    code, out = run("--file", margin, "--ceiling", "100")
    check("and prints the OK line too, alongside the WARN", "OK: %s is 99 words, ceiling 100" % margin in out and "WARN:" in out, out)

    outside = os.path.join(tmp, "outside.md")
    open(outside, "w").write((" ".join(["word"] * 97)) + "\n")
    code, out = run("--file", outside, "--ceiling", "100", "--quiet")
    check("a file just outside the margin prints nothing under --quiet", code == 0 and out == "", repr(out))

    over_ceiling = os.path.join(tmp, "over.md")
    open(over_ceiling, "w").write((" ".join(["word"] * 101)) + "\n")
    code, out = run("--file", over_ceiling, "--ceiling", "100")
    check("a file over the ceiling still fails, with no WARN in its place", code == 1 and "WARN:" not in out, out)

    big = os.path.join(tmp, "big.md"); open(big, "w").write("alpha beta gamma delta epsilon zeta eta\n")
    config = os.path.join(tmp, "quality.json")
    json.dump({"doc_size": [{"file": "small.md", "ceiling": 10}, {"file": "big.md", "ceiling": 6}]}, open(config, "w"))
    code, out = run("--config", config)
    check("a config with two documents fails when one is over", code == 1, out)
    check("and names the one over its ceiling", "FAIL: big.md is 7 words, over its ceiling of 6" in out, out)
    check("and not the other", "FAIL: small.md" not in out and "OK: small.md is 5 words, ceiling 10" in out, out)
    code, out = run("--config", config, "--file", small)
    check("--file without --ceiling takes the ceiling from the config", code == 0 and "is 5 words, ceiling 10" in out, out)

    code, out = run(cwd=REPO)
    real_docs = json.load(open(os.path.join(REPO, "quality.json"))).get("doc_size", [])
    check("this checkout's documents are under their ceilings",
          code == 0 and bool(real_docs) and all("OK: %s" % d["file"] in out for d in real_docs), out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print()
if failed: print("test-check-doc-size: %d case(s) failed." % failed); sys.exit(1)
print("test-check-doc-size: all cases passed.")

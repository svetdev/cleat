#!/usr/bin/env python3
"""test-check-manifests — assert quality/bin/check-manifests.py over a throwaway tree.

A pbxproj-shaped manifest naming every test file passes counting them; a test
file added to disk but not to the manifest fails naming it; an exempt file
passes and is counted; an exemption whose file is gone or now named fails; a
missing manifest is refused. Writes nothing outside a temporary directory.

  python3 quality/tests/test-check-manifests.py
"""
import json, os, shutil, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write, run as run_script
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "check-manifests.py")
suite = Suite("test-check-manifests"); check = suite.check


def run(config, *args):
    return run_script(SCRIPT, "--config", config, *args)


tmp = tempfile.mkdtemp(prefix="manifests-")
try:
    pbx = os.path.join(tmp, "App.xcodeproj", "project.pbxproj")
    write(pbx, "// !$*UTF8*$!\n\t\tAB1 /* StoreTests.swift */ = {isa = PBXFileReference; path = StoreTests.swift; };\n\t\tAB2 /* Thing.swift */ = {isa = PBXFileReference; path = Thing.swift; };\n")
    write(os.path.join(tmp, "AppTests", "StoreTests.swift"), "final class StoreTests {}\n")
    write(os.path.join(tmp, "App", "Thing.swift"), "struct Thing {}\n")
    config = os.path.join(tmp, "quality.json")
    entry = {"file": "App.xcodeproj/project.pbxproj", "roots": ["AppTests"], "extensions": [".swift"]}
    write(config, json.dumps({"manifests": [entry]}))
    code, out = run(config)
    check("a manifest naming every test file passes, counting them", code == 0 and "names all 1 source file(s)" in out, out)
    write(os.path.join(tmp, "AppTests", "NewTests.swift"), "final class NewTests {}\n")
    code, out = run(config)
    check("a test file on disk the manifest does not name fails, naming it", code == 1 and "AppTests/NewTests.swift" in out and "compiled into nothing" in out, out)
    check("and says what fixes it", "Regenerate the project" in out, out)
    entry["exempt"] = {"AppTests/NewTests.swift": "not yet"}
    write(config, json.dumps({"manifests": [entry]}))
    code, out = run(config)
    check("an exempt file passes and is counted", code == 0 and "(1 exempt)" in out, out)
    code, out = run(config, "--quiet")
    check("--quiet prints nothing on success", out == "", repr(out))
    write(pbx, open(pbx).read() + "\t\tAB3 /* NewTests.swift */ = {isa = PBXFileReference; path = NewTests.swift; };\n")
    code, out = run(config)
    check("an exemption whose file is now named fails", code == 1 and "gone or now in the manifest" in out and "AppTests/NewTests.swift" in out, out)
    write(config, json.dumps({"manifests": [{"file": "Gone.xcodeproj/project.pbxproj", "roots": ["AppTests"]}]}))
    code, out = run(config)
    check("a missing manifest is refused", code == 2 and "no such manifest" in out, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

#!/usr/bin/env python3
"""test-check-dead-symbols — assert quality/bin/check-dead-symbols.py.

Skipped case by case without ast-grep. Over a throwaway TypeScript tree: a
function referenced from another file is live; one referenced only by its
declaration is reported with its file and line; a name referenced only from a
test is live unless tests are excluded; an ignored name and an exempt name are
not reported; an exempt entry that is now referenced is noted; report mode
exits 0 and block mode exits 1. Writes nothing outside a temporary directory.

  python3 quality/tests/test-check-dead-symbols.py
"""
import json, os, shutil, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write, run as run_script
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "check-dead-symbols.py")
suite = Suite("test-check-dead-symbols"); check = suite.check

if not shutil.which("ast-grep"):
    check("ast-grep is not installed, so the dead-symbols report is not exercised (pip install ast-grep-cli)", True)
    suite.finish()


def run(config, *args):
    return run_script(SCRIPT, "--config", config, *args)


tmp = tempfile.mkdtemp(prefix="dead-symbols-")
try:
    src = os.path.join(tmp, "src")
    write(os.path.join(src, "lib.ts"), "export function usedHelper() {}\nexport function oldHelper() {}\nexport function specOnly() {}\nexport function createClient() {}\nexport class Widget {}\nfunction main() {}\n")
    write(os.path.join(src, "app.ts"), "import { usedHelper, Widget } from './lib';\nusedHelper(); new Widget();\n")
    write(os.path.join(src, "lib.test.ts"), "import { specOnly } from './lib';\nspecOnly();\n")
    config = os.path.join(tmp, "quality.json")
    section = {"roots": ["src"], "language": "typescript", "exempt": {}}
    write(config, json.dumps({"dead_symbols": section}))
    code, out = run(config)
    check("report mode exits 0 and names the symbol nothing references, with its file and line", code == 0 and "REPORT: 2 declared symbol(s)" in out and "src/lib.ts:2  oldHelper" in out and "src/lib.ts:4  createClient" in out, out)
    check("a symbol referenced from another file is live, and `main` is ignored", "usedHelper" not in out and "Widget" not in out and "main" not in out, out)
    check("a symbol referenced only from a test is live while tests are swept", "specOnly" not in out, out)
    section["exclude"] = ["*.test.ts"]
    write(config, json.dumps({"dead_symbols": section}))
    code, out = run(config)
    check("with tests excluded it is reported", "src/lib.ts:3  specOnly" in out, out)
    section["exempt"] = {"src/lib.ts:createClient": "public API", "src/lib.ts:usedHelper": "stale"}
    section["ignore"] = ["Only$"]
    write(config, json.dumps({"dead_symbols": section}))
    code, out = run(config)
    check("an exempt name and an ignored name are not reported", "createClient" not in out.split("NOTE")[0] and "specOnly" not in out, out)
    check("an exempt entry that is referenced is noted", "NOTE: 1 exempt entry is now referenced" in out and "usedHelper" in out, out)
    section["enforcement"] = "block"
    write(config, json.dumps({"dead_symbols": section}))
    code, out = run(config)
    check("block mode fails on what remains", code == 1 and "FAIL: 1 declared symbol(s)" in out and "oldHelper" in out, out)
    section["exempt"]["src/lib.ts:oldHelper"] = "kept on purpose"
    write(config, json.dumps({"dead_symbols": section}))
    code, out = run(config, "--quiet")
    check("with everything exempt or ignored the report is empty and --quiet prints only the note", code == 0 and "REPORT" not in out, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

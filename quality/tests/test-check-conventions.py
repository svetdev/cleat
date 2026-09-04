#!/usr/bin/env python3
"""test-check-conventions — assert quality/bin/check-conventions.py.

Two project rules over a throwaway tree: every site is found under its rule;
the failure prints each broken rule's message, which is what the agent reads;
--write-baseline accepts the sites; the same tree passes; a new site fails
naming the rule and its message and never the accept command; a rule's
exclude glob and extensions hold; --only judges the named files against only
their entries; a rule missing its message is refused. Writes nothing outside
a temporary directory.

  python3 quality/tests/test-check-conventions.py
"""
import json, os, shutil, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write, run as run_script
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "check-conventions.py")
suite = Suite("test-check-conventions"); check = suite.check


def run(config, *args):
    return run_script(SCRIPT, "--config", config, *args)


tmp = tempfile.mkdtemp(prefix="conventions-")
try:
    write(os.path.join(tmp, "src", "app.py"), "import vendor_sdk\nfrom lib.client import Client\n")
    write(os.path.join(tmp, "src", "clients", "vendor.py"), "import vendor_sdk  # the one place allowed\n")
    write(os.path.join(tmp, "src", "ui.ts"), "console.log('x');\nlog.info('y');\n")
    write(os.path.join(tmp, "src", "notes.md"), "import vendor_sdk\n")
    config = os.path.join(tmp, "quality.json")
    rules = [{"name": "vendor sdk", "pattern": r"^\s*(?:from|import)\s+vendor_sdk\b", "roots": ["src"], "extensions": [".py"],
              "exclude": ["*/clients/*"], "message": "Import the client from lib/client; only clients/ touches the vendor SDK."},
             {"name": "console.log", "pattern": r"console\.log\(", "roots": ["src"], "languages": ["typescript"], "message": "Use the logger from lib/log."}]
    write(config, json.dumps({"conventions": {"rules": rules, "baseline": "conventions-baseline.json"}}))
    code, out = run(config)
    check("every site is found under its rule", code == 1 and "src/app.py:1  vendor sdk" in out and "src/ui.ts:1  console.log" in out, out)
    check("a rule's exclude glob and extensions hold", "clients/vendor.py" not in out and "notes.md" not in out, out)
    check("the failure prints each broken rule's message", "vendor sdk — Import the client from lib/client" in out and "console.log — Use the logger from lib/log." in out, out)
    check("and never the accept command", "--write-baseline" not in out, out)
    code, out = run(config, "--write-baseline")
    check("--write-baseline accepts the sites", code == 0 and "2 site(s) accepted across 2 rule(s)" in out, out)
    code, out = run(config)
    check("the same tree passes", code == 0 and "all 2 in the baseline" in out, out)
    write(os.path.join(tmp, "src", "app.py"), "import vendor_sdk\nfrom lib.client import Client\nimport vendor_sdk as v\n")
    code, out = run(config)
    check("a new site fails naming the rule", code == 1 and "src/app.py:3  vendor sdk" in out and "1 new site(s)" in out, out)
    check("with only that rule's message", "Import the client" in out and "Use the logger" not in out, out)
    code, out = run(config, "--only", "src/ui.ts")
    check("--only judges the named files against only their entries", code == 0 and "all 1 in the baseline" in out, out)
    write(config, json.dumps({"conventions": {"rules": [{"name": "x", "pattern": "y"}], "baseline": "b.json"}}))
    code, out = run(config)
    check("a rule without a message is refused", code == 2 and '"message"' in out, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

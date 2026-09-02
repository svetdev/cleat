#!/usr/bin/env python3
"""test-check-inventory — assert quality/bin/check-inventory.py over a throwaway cache.

A directory's matching entries are recorded; the same directory passes; an
entry gone fails naming it and what restores it; a new entry is a NOTE and a
--strict failure until recorded; a file outside the pattern is ignored; a
missing directory and an unknown gate are refused. Writes nothing outside a
temporary directory.

  python3 quality/tests/test-check-inventory.py
"""
import json, os, shutil, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write, run as run_script
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "check-inventory.py")
suite = Suite("test-check-inventory"); check = suite.check


def run(config, *args):
    return run_script(SCRIPT, "--config", config, *args)


tmp = tempfile.mkdtemp(prefix="inventory-")
try:
    for name in ("query-a1.json", "query-b2.json", "README.md"):
        write(os.path.join(tmp, ".sqlx", name), "{}\n")
    config = os.path.join(tmp, "quality.json")
    write(config, json.dumps({"inventory": [{"name": "sqlx", "path": ".sqlx", "pattern": "query-*.json", "baseline": "inventory-sqlx.json"}]}))
    code, out = run(config, "--gate", "sqlx", "--write-baseline")
    check("the matching entries are recorded, a file outside the pattern ignored", code == 0 and "2 entries under .sqlx recorded" in out, out)
    code, out = run(config, "--gate", "sqlx", "--strict")
    check("the same directory passes", code == 0 and "all 2 recorded entries under .sqlx still there" in out, out)
    os.remove(os.path.join(tmp, ".sqlx", "query-a1.json"))
    code, out = run(config, "--gate", "sqlx")
    check("an entry gone fails naming it", code == 1 and ".sqlx/query-a1.json" in out and "a tool rewrote the directory" in out, out)
    check("and says what restores it", "re-run what produced them" in out, out)
    write(os.path.join(tmp, ".sqlx", "query-a1.json"), "{}\n"); write(os.path.join(tmp, ".sqlx", "query-c3.json"), "{}\n")
    code, out = run(config, "--gate", "sqlx")
    check("a new entry is a NOTE with the recording command", code == 0 and "1 new entry" in out and "--write-baseline" in out, out)
    code, out = run(config, "--gate", "sqlx", "--strict")
    check("and a --strict failure until recorded", code == 1, out)
    code, out = run(config, "--gate", "nope")
    check("an unknown gate is refused", code == 2 and "sqlx" in out, out)
    write(config, json.dumps({"inventory": [{"name": "gone", "path": "nowhere", "baseline": "b.json"}]}))
    code, out = run(config, "--gate", "gone")
    check("a missing directory is refused", code == 2 and "no such directory" in out, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

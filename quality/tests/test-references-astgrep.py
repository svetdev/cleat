#!/usr/bin/env python3
"""test-references-astgrep — assert the ast-grep reader in extractors/references.py,
through check-layering.py and check-reachability.py.

Skipped case by case when ast-grep is not installed (`pip install ast-grep-cli`).
Over throwaway Kotlin and Rust trees: a declaration's name is read from the
grammar, a modifier before it notwithstanding; an identifier in another file
is a reference and one in a comment or a string is not; a lower layer
referencing a higher one fails through check-layering; a file nothing
references fails through check-reachability; an unknown language is refused.
Writes nothing outside a temporary directory.

  python3 quality/tests/test-references-astgrep.py
"""
import json, os, shutil, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write, run
BIN = os.path.join(os.path.dirname(HERE), "bin")
sys.path.insert(0, BIN)
from extractors import references
suite = Suite("test-references-astgrep"); check = suite.check

if not shutil.which("ast-grep"):
    check("ast-grep is not installed, so the ast-grep reader is not exercised (pip install ast-grep-cli)", True)
    suite.finish()

tmp = tempfile.mkdtemp(prefix="astgrep-")
try:
    kt = os.path.join(tmp, "kt")
    write(os.path.join(kt, "models", "Thing.kt"), "data class Thing(val s: Store)  // reaches up to a service\n")
    write(os.path.join(kt, "services", "Store.kt"), 'internal class Store { fun run() { val t = "Thing" } }  // Thing only in a string and here\n')
    write(os.path.join(kt, "services", "Orphan.kt"), "object OrphanService\n")
    write(os.path.join(kt, "Main.kt"), "fun main() { Store().run() }\n")
    refs = references.astgrep_references(kt, [kt], "kotlin", set())
    names = sorted((os.path.relpath(f, kt), n, os.path.relpath(t, kt)) for f, _l, n, t, _r in refs)
    check("a data class, an internal class and a method are declared under their modifiers, and referenced by name",
          names == [("Main.kt", "Store", "services/Store.kt"), ("Main.kt", "run", "services/Store.kt"), ("models/Thing.kt", "Store", "services/Store.kt")], str(names))
    check("a name in a string or a comment is not a reference", not any(n == "Thing" for _, n, _ in names), str(names))

    config = os.path.join(tmp, "quality.json")
    write(config, json.dumps({"layering": {"references": "ast-grep", "language": "kotlin", "skip_dirs": [],
        "allowed": {"models": [], "services": ["models"], "App": None}, "always_allowed": [],
        "roots": [{"root": "kt", "exempt": []}]}}))
    code, out = run(os.path.join(BIN, "check-layering.py"), "--config", config)
    check("through check-layering, a model reaching a service fails naming the reference", code == 1 and "models/Thing.kt:1 -> Store (services/Store.kt)" in out, out)
    write(config, json.dumps({"reachability": {"roots": ["kt"], "pattern": "services/*", "references": "ast-grep", "language": "kotlin", "exempt": {}}}))
    code, out = run(os.path.join(BIN, "check-reachability.py"), "--config", config)
    check("through check-reachability, the service nothing names fails and the used one does not", code == 1 and "kt/services/Orphan.kt" in out and "Store.kt" not in out, out)

    rs = os.path.join(tmp, "rs")
    write(os.path.join(rs, "models.rs"), "pub struct Thing { s: Store }\n")
    write(os.path.join(rs, "services.rs"), "pub struct Store;\nimpl Store { pub fn run(&self) {} }\n")
    refs = references.astgrep_references(rs, [rs], "rust", set())
    check("rust: a pub struct behind its modifier is declared and referenced", [(os.path.basename(f), n) for f, _l, n, t, _r in refs] == [("models.rs", "Store")], str(refs))
    write(config, json.dumps({"reachability": {"roots": ["rs"], "pattern": "*", "references": "ast-grep", "language": "cobol", "exempt": {}}}))
    code, out = run(os.path.join(BIN, "check-reachability.py"), "--config", config)
    check("an unknown language is refused naming the known ones", code == 2 and "cobol" in out and "kotlin" in out, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

#!/usr/bin/env python3
"""test-layering-imports — assert check-layering.py reading references from imports.

Over throwaway Python, TypeScript, Rust, Go and Kotlin trees laid out in
layers: a lower layer importing a higher one fails naming the file, line and
import; the allowed direction passes; a relative, an aliased and a
package-style import all resolve; an import of something outside the judged
roots is ignored; a comment is not an import; an unknown language is refused.
Writes nothing outside a temporary directory.

  python3 quality/tests/test-layering-imports.py
"""
import json, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "check-layering.py")
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
from extractors import references

suite = Suite("test-layering-imports"); check = suite.check



def config_for(tmp, root, language, exempt=()):
    path = os.path.join(tmp, "quality.json")
    write(path, json.dumps({"layering": {
        "references": "imports", "language": language, "skip_dirs": [],
        "allowed": {"models": [], "services": ["models"], "views": ["services", "models"], "App": None},
        "always_allowed": [], "roots": [{"root": os.path.relpath(root, tmp), "exempt": list(exempt)}]}}))
    return path


def run(config, *args):
    proc = subprocess.run([sys.executable, SCRIPT, "--config", config, *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


tmp = tempfile.mkdtemp(prefix="layering-imports-")
try:
    # ---- python: a model importing a service is the violation; the rest is allowed
    py = os.path.join(tmp, "py", "app")
    write(os.path.join(py, "models", "thing.py"), "from app.services.store import Store  # a model reaching up\n\nclass Thing:\n    pass\n")
    write(os.path.join(py, "services", "store.py"), "from ..models.thing import Thing\nimport json\n# from app.views.page import Page — a comment, not an import\n\nclass Store:\n    pass\n")
    write(os.path.join(py, "views", "page.py"), "from app.services import store\nfrom app.models.thing import Thing\n")
    write(os.path.join(py, "main.py"), "from app.views.page import x\n")
    config = config_for(tmp, py, "python")
    code, out = run(config)
    check("python: a model importing a service fails, naming the file, line and import", code == 1 and "models/thing.py:1 -> store (services/store.py)" in out, out)
    check("python: the allowed direction, a relative import and a package import all resolve, and a comment does not", "views/page.py" not in out and "services/store.py:" not in out, out)
    refs = references.import_references(py, [py], "python", set())
    check("python: four references read — stdlib ignored, a package with no __init__.py unresolved", len(refs) == 4 and all(r[3].startswith(py) for r in refs), str(refs))
    code, out = run(config_for(tmp, py, "python", exempt=[{"file": "models/thing.py", "name": "store", "reason": "legacy"}]))
    check("python: an exempt pair passes and is counted", code == 0 and "4 cross-file references obey the layering (1 exempt)" in out, out)

    # ---- typescript: relative and aliased specifiers, index files, .js extension on a .ts file
    ts = os.path.join(tmp, "ts", "src")
    write(os.path.join(ts, "models", "thing.ts"), "import { Store } from '../services/store';\nexport class Thing {}\n")
    write(os.path.join(ts, "services", "store.ts"), "import { Thing } from '@/models/thing.js';\nimport fs from 'fs';\nexport class Store {}\n")
    write(os.path.join(ts, "views", "index.ts"), "export * from '../services';\n")
    write(os.path.join(ts, "services", "index.ts"), "export { Store } from './store';\n")
    code, out = run(config_for(tmp, ts, "typescript"))
    check("typescript: a model importing a service fails", code == 1 and "models/thing.ts:1 -> store (services/store.ts)" in out, out)
    check("typescript: an alias, a .js suffix, an index file and a bare package resolve or are ignored as they should", "services/store.ts:" not in out and "views/index.ts" not in out, out)

    # ---- rust: crate:: paths and mod declarations
    rs = os.path.join(tmp, "rs")
    write(os.path.join(rs, "src", "models", "mod.rs"), "use crate::services::store::Store;\npub struct Thing;\n")
    write(os.path.join(rs, "src", "services", "store.rs"), "use crate::models::Thing;\nuse std::collections::HashMap;\npub struct Store;\n")
    write(os.path.join(rs, "src", "services", "mod.rs"), "pub mod store;\n")
    write(os.path.join(rs, "src", "main.rs"), "mod models;\nmod services;\n")
    rs_config = os.path.join(tmp, "quality.json")
    write(rs_config, json.dumps({"layering": {"references": "imports", "language": "rust", "skip_dirs": [],
        "allowed": {"models": [], "services": ["models"], "App": None}, "always_allowed": [],
        "roots": [{"root": "rs/src", "exempt": []}]}}))
    code, out = run(rs_config)
    check("rust: a model using a service fails", code == 1 and "models/mod.rs:1 -> Store (services/store.rs)" in out, out)
    check("rust: std is ignored and mod declarations resolve", "services/store.rs:" not in out and "main.rs" not in out, out)

    # ---- go: package paths resolved by suffix under the root
    go = os.path.join(tmp, "go")
    write(os.path.join(go, "models", "thing.go"), 'package models\n\nimport (\n\t"fmt"\n\t"example.com/app/services"\n)\n')
    write(os.path.join(go, "services", "store.go"), 'package services\n\nimport "example.com/app/models"\n')
    code, out = run(config_for(tmp, go, "go"))
    check("go: a model importing a service package fails, at the line inside the import block", code == 1 and "models/thing.go:5 -> services (services)" in out, out)

    # ---- kotlin: package-qualified imports, with the package prefix stripped
    kt = os.path.join(tmp, "kt")
    write(os.path.join(kt, "models", "Thing.kt"), "package com.acme.models\nimport com.acme.services.Store\nclass Thing\n")
    write(os.path.join(kt, "services", "Store.kt"), "package com.acme.services\nimport com.acme.models.Thing\nimport java.util.List\nclass Store\n")
    code, out = run(config_for(tmp, kt, "kotlin"))
    check("kotlin: a model importing a service fails", code == 1 and "models/Thing.kt:2 -> Store (services/Store.kt)" in out, out)

    code, out = run(config_for(tmp, kt, "cobol"))
    check("an unknown language is refused naming the known ones", code == 2 and "cobol" in out and "python" in out, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

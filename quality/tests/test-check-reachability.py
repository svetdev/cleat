#!/usr/bin/env python3
"""test-check-reachability — assert quality/bin/check-reachability.py.

Over a throwaway Swift tree: a service the app constructs is reached; one
nothing names is reported with the types it declares; one named only in a
comment is unreached, since prose is not construction; a service in a second
root reached from the first is reached; an exempt file passes and is counted;
an exemption whose file is now reached, or gone, fails naming why; and the
same judgment over a Python tree by imports. Writes nothing outside a
temporary directory.

  python3 quality/tests/test-check-reachability.py
"""
import json, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write, run as run_script
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "check-reachability.py")
suite = Suite("test-check-reachability"); check = suite.check


def configure(tmp, section):
    path = os.path.join(tmp, "quality.json")
    write(path, json.dumps({"reachability": section}))
    return path


def run(config, *args):
    return run_script(SCRIPT, "--config", config, *args)


tmp = tempfile.mkdtemp(prefix="reachability-")
try:
    app = os.path.join(tmp, "App"); core = os.path.join(tmp, "Core", "Sources", "Core")
    write(os.path.join(app, "AcmeApp.swift"), "@main\nstruct AcmeApp {\n    let wired = WiredService()\n    let core = CoreStore()\n    // OrphanService is only talked about here\n}\n")
    write(os.path.join(app, "Services", "Wired.swift"), "struct WiredService {}\n")
    write(os.path.join(app, "Services", "Orphan.swift"), "enum OrphanKind {}\nfinal class OrphanService {}\n")
    write(os.path.join(app, "Services", "Talked.swift"), "struct TalkedService {}\n")
    write(os.path.join(app, "Models", "Thing.swift"), "struct Thing {}\n")
    write(os.path.join(core, "Services", "Store.swift"), "public final class CoreStore {}\n")
    write(os.path.join(core, "Services", "Lonely.swift"), "public struct LonelyService {}\n")
    section = {"roots": ["App", "Core/Sources/Core"], "pattern": "Services/*", "references": "identifiers",
               "language": "swift", "extensions": [".swift"], "exempt": {}}
    code, out = run(configure(tmp, section))
    check("services nothing constructs fail, with the types they declare", code == 1 and "App/Services/Orphan.swift (declares OrphanKind, OrphanService)" in out, out)
    check("a service named only in a comment is unreached", "App/Services/Talked.swift" in out, out)
    check("a service reached from another root is reached", "Store.swift" not in out and "Wired.swift" not in out, out)
    check("a package service nothing names is reported too", "Core/Sources/Core/Services/Lonely.swift" in out, out)
    check("a file outside the pattern is not judged", "Thing.swift" not in out, out)
    check("the count is in the message", "3 file(s) matching Services/*" in out, out)

    section["exempt"] = {"App/Services/Orphan.swift": "tracked as #1", "App/Services/Talked.swift": "docs only", "Core/Sources/Core/Services/Lonely.swift": "next release"}
    code, out = run(configure(tmp, section))
    check("with the unreached files exempt the check passes and counts them", code == 0 and "5 file(s) matching Services/*, all reached (3 exempt)" in out, out)
    code, out = run(configure(tmp, section), "--quiet")
    check("--quiet prints nothing on success", out == "", repr(out))

    write(os.path.join(app, "AcmeApp.swift"), "@main\nstruct AcmeApp {\n    let wired = WiredService()\n    let core = CoreStore()\n    let orphan = OrphanService()\n}\n")
    code, out = run(configure(tmp, section))
    check("an exemption whose file is now reached fails naming why", code == 1 and "App/Services/Orphan.swift — now reached" in out, out)
    os.remove(os.path.join(app, "Services", "Talked.swift"))
    code, out = run(configure(tmp, section))
    check("an exemption whose file is gone fails naming why", "App/Services/Talked.swift — no such judged file" in out, out)

    # ---- by imports
    py = os.path.join(tmp, "py")
    write(os.path.join(py, "main.py"), "from services.used import Used\n")
    write(os.path.join(py, "services", "used.py"), "class Used: pass\n")
    write(os.path.join(py, "services", "orphan.py"), "class Orphan: pass\n")
    code, out = run(configure(tmp, {"roots": ["py"], "pattern": "services/*", "references": "imports", "language": "python", "exempt": {}}))
    check("by imports: a module nothing imports fails, a used one does not", code == 1 and "py/services/orphan.py" in out and "used.py" not in out, out)
    tsx = os.path.join(tmp, "web")
    write(os.path.join(tsx, "components", "Button.tsx"), "export const Button = () => null;\n")
    write(os.path.join(tsx, "components", "Button.test.tsx"), "import { Button } from './Button';\nimport { Lonely } from './Lonely';\n")
    write(os.path.join(tsx, "components", "Lonely.tsx"), "export const Lonely = () => null;\n")
    write(os.path.join(tsx, "App.tsx"), "import { Button } from './components/Button';\n")
    code, out = run(configure(tmp, {"roots": ["web"], "pattern": "components/*", "references": "imports", "language": "typescript", "exempt": {}}))
    check("without an exclude, a test file is judged as unreached and its imports count as reaching", code == 1 and "Button.test.tsx" in out and "Lonely.tsx" not in out, out)
    code, out = run(configure(tmp, {"roots": ["web"], "pattern": "components/*", "references": "imports", "language": "typescript", "exclude": ["**/*.test.tsx", "*.test.tsx", "components/*.test.tsx"], "exempt": {}}))
    check("with exclude globs, test files are neither judged nor read for references", code == 1 and "Button.test.tsx" not in out and "web/components/Lonely.tsx" in out, out)
    code, out = run(configure(tmp, {"roots": ["py"], "pattern": "services/*", "references": "imports", "language": "cobol", "exempt": {}}))
    check("an unknown language is refused", code == 2 and "cobol" in out, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

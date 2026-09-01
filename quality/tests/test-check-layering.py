#!/usr/bin/env python3
"""test-check-layering — assert the reading and the ratchet in quality/bin/check-layering.py.

The check is regex over a judged tree and its failure mode is silence: a
`DECL_RE` that stops matching a declaration, or a `strip_code` that eats a
line, and it goes on printing `OK: N cross-file references obey the layering`
while judging nothing. So what counts as a reference, what does not (a
comment, a string), which line is reported, and both halves of the ratchet —
an exempt pair passing, a dead exemption failing — are driven here through the
real script against a throwaway tree handed to it with `--app`, under a
fixture `quality.json` this test writes and hands over with `--config`. The
fixture's `layering` section carries the layer table the check was written for
and two judged roots, each with an exemption list of its own, so the cases
keep meaning the same thing after the project's lists change and so the
per-root isolation — an exemption written for one root is not read against the
other — has something to prove itself against. Last, the script is run against
this checkout's own `quality.json` at the repository root and must exit 0, so
a reference somebody adds across the layering in either root fails the
preflight here rather than being discovered later.

It starts no build, reads no process list and writes nothing outside a temporary
directory, so it is safe on its own or as part of the suite while the app is
running.

  ./quality/tests/test-check-layering.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(REPO, "quality", "bin", "check-layering.py")

failed = 0


def check(name, ok, detail=""):
    global failed
    if ok:
        print("  ok    %s" % name)
    else:
        print("  FAIL  %s%s" % (name, "\n          " + detail if detail else ""))
        failed += 1


def write(root, relative, text):
    path = os.path.join(root, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)


# The first root's exemption list: a pair the fixtures below can reproduce,
# and one naming a file no fixture tree has.
EXEMPT_SOURCE, EXEMPT_TYPE = "Models/Exempted.swift", "LegacyService"
ABSENT_SOURCE, ABSENT_TYPE = "Models/Absent.swift", "LegacyService"

# The second root's exemption list — its own pairs, distinct from the first
# root's, so a test can tell whether an entry leaked across roots.
ROOT2_EXEMPT_SOURCE, ROOT2_EXEMPT_TYPE = "Models/PackageExempted.swift", "PackageLegacyService"
ROOT2_ABSENT_SOURCE, ROOT2_ABSENT_TYPE = "Models/PackageAbsent.swift", "PackageLegacyService"


def write_config(tmp, root, root2):
    """A quality.json in `tmp` naming two judged roots — `root` (the app
    stand-in, carrying the pair the --app cases below reproduce) and `root2`
    (the package stand-in, carrying a pair of its own)."""
    path = os.path.join(tmp, "quality.json")
    with open(path, "w") as handle:
        json.dump({
            "layering": {
                "skip_dirs": ["Frameworks"],
                "allowed": {
                    "Extensions": [],
                    "Models": ["Extensions"],
                    "Runtime": ["Models", "Extensions"],
                    "Services": ["Runtime", "Models", "Extensions"],
                    "ViewModels": ["Services", "Runtime", "Models", "Extensions"],
                    "Views": None,
                    "App": None,
                    "Testing": None,
                },
                "always_allowed": ["Testing"],
                "roots": [
                    {
                        "root": root,
                        "exempt": [
                            {"file": EXEMPT_SOURCE, "name": EXEMPT_TYPE, "reason": "a model calling a service; inject it"},
                            {"file": ABSENT_SOURCE, "name": ABSENT_TYPE, "reason": "a file no fixture tree has"},
                        ],
                    },
                    {
                        "root": root2,
                        "exempt": [
                            {"file": ROOT2_EXEMPT_SOURCE, "name": ROOT2_EXEMPT_TYPE, "reason": "a package model calling a service; inject it"},
                            {"file": ROOT2_ABSENT_SOURCE, "name": ROOT2_ABSENT_TYPE, "reason": "a file no package fixture tree has"},
                        ],
                    },
                ],
            }
        }, handle)
    return path


def run(config, *args):
    proc = subprocess.run([sys.executable, SCRIPT, "--config", config, *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


tmp = tempfile.mkdtemp(prefix="check-layering-")
try:
    # The configured roots are trees none of the --app cases judge, so an entry
    # whose file a fixture lacks stays the fixture's business alone.
    config = write_config(tmp, os.path.join(tmp, "rooted"), os.path.join(tmp, "rooted2"))

    # --- A model reaching into a service: the reference this check exists to catch.
    app = os.path.join(tmp, "bad")
    write(app, "Services/Clock.swift", "struct Clock {}\n")
    write(app, "Models/Thing.swift", "import Foundation\n\nstruct Thing {\n    let clock = Clock()\n}\n")
    code, out = run(config, "--app", app)
    check("a model referencing a service fails", code == 1, out)
    check("the offending file, line and type are named", "Models/Thing.swift:4 -> Clock (Services/Clock.swift)" in out, out)
    check("and the fix points at the config's exempt list", "`exempt` list" in out and config in out, out)

    # --- The same shape, standing in for the package: the rule is shared by
    # every root, not something only the app tree gets judged against.
    app = os.path.join(tmp, "bad-package")
    write(app, "Services/Store.swift", "struct Store {}\n")
    write(app, "Models/BedrockConfig.swift", "import Foundation\n\nstruct SpawnTimeConfigRead {\n    let s = Store()\n}\n")
    code, out = run(config, "--app", app)
    check("a package Models file referencing a package Service fails", code == 1, out)
    check("named with its path and line", "Models/BedrockConfig.swift:4 -> Store (Services/Store.swift)" in out, out)

    # --- Only code counts: the same name in a comment and a string is not a reference.
    app = os.path.join(tmp, "prose")
    write(app, "Services/Clock.swift", "struct Clock {}\n")
    write(app, "Models/Thing.swift", '/// Unlike Clock, a Thing is a value.\nstruct Thing {\n    let note = "set by Clock"\n    // Clock again\n}\n')
    code, out = run(config, "--app", app)
    check("a name in a comment or a string is not a reference", code == 0, out)

    # --- The allowed direction, and a layer depending on itself.
    app = os.path.join(tmp, "good")
    write(app, "Models/Thing.swift", "struct Thing {}\n")
    write(app, "Models/Other.swift", "struct Other { let t = Thing() }\n")
    write(app, "Services/Clock.swift", "struct Clock { let t = Thing() }\n")
    write(app, "Views/ThingView.swift", "struct ThingView { let c = Clock(); let t = Thing() }\n")
    code, out = run(config, "--app", app)
    check("downward and same-layer references pass", code == 0, out)
    check("the success line counts the references judged", "OK: 4 cross-file references obey the layering (0 exempt)" in out, out)
    code, out = run(config, "--app", app, "--quiet")
    check("--quiet prints nothing on success", code == 0 and out == "", repr(out))

    # --- A skipped directory is not read.
    write(app, "Frameworks/Vendored.swift", "struct Vendored { let t = Thing() }\n")
    code, out = run(config, "--app", app)
    check("a directory in skip_dirs is not read", "OK: 4 cross-file" in out, out)

    # --- Testing/ is reachable from anywhere.
    app = os.path.join(tmp, "testing")
    write(app, "Testing/Switch.swift", "enum TestSwitch {}\n")
    write(app, "Models/Thing.swift", "struct Thing { let s = TestSwitch.self }\n")
    code, out = run(config, "--app", app)
    check("a model referencing Testing/ passes", code == 0, out)

    # --- The ratchet: a pair on the list passes; the same pair off it would fail.
    app = os.path.join(tmp, "exempt")
    write(app, "Services/Owner.swift", "struct %s {}\n" % EXEMPT_TYPE)
    write(app, EXEMPT_SOURCE, "struct Ref { let x = %s() }\n" % EXEMPT_TYPE)
    code, out = run(config, "--app", app)
    check("an exempt pair passes", code == 0, out)
    check("and is counted as exempt", "(1 exempt)" in out, out)
    # The same file with the reference taken out: the entry now names nothing,
    # and that fails — the way the list gets shorter is somebody deleting it.
    write(app, EXEMPT_SOURCE, "struct Ref {}\n")
    code, out = run(config, "--app", app)
    check("a dead exemption fails the check", code == 1, out)
    check("and is named", "(%r, %r)" % (EXEMPT_SOURCE, EXEMPT_TYPE) in out, out)
    # Entries whose files the fixture does not have are not the fixture's business.
    check("exemptions for files the tree lacks are not reported", out.count("\n  (") == 1, out)

    # --- The configured roots, judged with no --app: here a missing file is
    # dead too, and an entry written for one root is not read against the
    # other's tree.
    rooted = os.path.join(tmp, "rooted")
    write(rooted, "Services/Owner.swift", "struct %s {}\n" % EXEMPT_TYPE)
    write(rooted, EXEMPT_SOURCE, "struct Ref { let x = %s() }\n" % EXEMPT_TYPE)
    rooted2 = os.path.join(tmp, "rooted2")
    write(rooted2, "Services/Owner.swift", "struct %s {}\n" % ROOT2_EXEMPT_TYPE)
    write(rooted2, ROOT2_EXEMPT_SOURCE, "struct Ref { let x = %s() }\n" % ROOT2_EXEMPT_TYPE)
    code, out = run(config)
    check("without --app both configured roots are judged", code == 1, out)
    check("root 1's exemption for a file it lacks is dead", "(%r, %r)" % (ABSENT_SOURCE, ABSENT_TYPE) in out, out)
    check("root 2's exemption for a file it lacks is dead", "(%r, %r)" % (ROOT2_ABSENT_SOURCE, ROOT2_ABSENT_TYPE) in out, out)
    check("root 1's live exemption is not reported dead", "(%r, %r)" % (EXEMPT_SOURCE, EXEMPT_TYPE) not in out, out)
    check("root 2's live exemption is not reported dead", "(%r, %r)" % (ROOT2_EXEMPT_SOURCE, ROOT2_EXEMPT_TYPE) not in out, out)
    check("exactly the two dead entries are named, one per root", out.count("\n  (") == 2, out)

    # --- Owners are resolved across every configured root at once: a type
    # declared under one root is still seen by a disallowed-layer reference to
    # it from another root, not lost the way it would be if each root's owners
    # were read in isolation.
    CROSS_TYPE, CROSS_SOURCE = "CrossType", "Runtime/Consumer.swift"
    cross_a = os.path.join(tmp, "cross-a")
    cross_b = os.path.join(tmp, "cross-b")
    write(cross_b, "Services/Store.swift", "struct %s {}\n" % CROSS_TYPE)
    write(cross_a, CROSS_SOURCE, "struct Consumer { let s = %s() }\n" % CROSS_TYPE)

    def write_cross_config(exempt):
        path = os.path.join(tmp, "cross.json")
        with open(path, "w") as handle:
            json.dump({
                "layering": {
                    "skip_dirs": [],
                    "allowed": {
                        "Extensions": [],
                        "Models": ["Extensions"],
                        "Runtime": ["Models", "Extensions"],
                        "Services": ["Runtime", "Models", "Extensions"],
                        "ViewModels": ["Services", "Runtime", "Models", "Extensions"],
                        "Views": None,
                        "App": None,
                        "Testing": None,
                    },
                    "always_allowed": ["Testing"],
                    "roots": [
                        {"root": cross_a, "exempt": exempt},
                        {"root": cross_b, "exempt": []},
                    ],
                }
            }, handle)
        return path

    code, out = run(write_cross_config([]))
    check("a Runtime file naming a type declared in the other root's Services fails",
          code == 1 and "%s:1 -> %s (Services/Store.swift)" % (CROSS_SOURCE, CROSS_TYPE) in out, out)
    code, out = run(write_cross_config(
        [{"file": CROSS_SOURCE, "name": CROSS_TYPE, "reason": "a runtime file naming a type that moved into the other root; inject it"}]
    ))
    check("the pair passes once exempt in its own root", code == 0, out)

    # --- A config without the section, without the roots key, or an entry
    # short of a field, is an error, not a pass.
    with open(os.path.join(tmp, "empty.json"), "w") as handle:
        json.dump({}, handle)
    code, out = run(os.path.join(tmp, "empty.json"), "--app", app)
    check("a config with no layering section fails naming it", code == 2 and '"layering"' in out, out)

    with open(os.path.join(tmp, "no-roots.json"), "w") as handle:
        json.dump({"layering": {"skip_dirs": [], "allowed": {}, "always_allowed": []}}, handle)
    code, out = run(os.path.join(tmp, "no-roots.json"), "--app", app)
    check("a layering section without \"roots\" fails naming the key rather than falling back to a default",
          code == 2 and '"roots"' in out, out)

    with open(os.path.join(tmp, "short.json"), "w") as handle:
        json.dump({"layering": {"skip_dirs": [], "allowed": {}, "always_allowed": [],
                                "roots": [{"root": app, "exempt": [{"file": EXEMPT_SOURCE, "name": EXEMPT_TYPE}]}]}}, handle)
    code, out = run(os.path.join(tmp, "short.json"), "--app", app)
    check("an exempt entry without a reason fails naming the shape", code == 2 and '"reason"' in out, out)

    # --- Against this checkout: green, or the preflight stops here. A checkout whose
    # quality.json has no "layering" section (a stack where the crate graph is the
    # layering) is not judged — the section's absence is that project's decision.
    checkout_config = os.path.join(REPO, "quality.json")
    has_section = os.path.isfile(checkout_config) and "layering" in json.load(open(checkout_config))
    if has_section:
        proc = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, cwd=REPO)
        check("this checkout obeys the layering", proc.returncode == 0, proc.stdout + proc.stderr)
        lines = re.findall(r"OK: .+: \d+ cross-file references obey the layering \(\d+ exempt\)", proc.stdout)
        check("its success line reports judged and exempt counts for each configured root", len(lines) >= 1, proc.stdout)
    else:
        check("this checkout configures no layering section, so it is not judged", True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failed:
    print("test-check-layering: %d case(s) failed." % failed)
    sys.exit(1)
print("test-check-layering: all cases passed.")

#!/usr/bin/env python3
"""test-check-features-map — assert the passes in quality/bin/check-features-map.py.

The third pass is why this exists. The first two read the feature map, so what
they judge is bounded by what somebody wrote there: nine services under
`Kiteloop/Kiteloop/Services/` were compiled, unit-tested and constructed by
nothing the app runs, and the check reported OK because no `[x]` row happened to
cite them. The pass added for that walks `Services/` under every root in
`"service_roots"` instead — the app target and the host-less package both, so a
package service reached by nothing is caught the same way an app one is — and
it lands as a ratchet — those nine were exempt by name — so the one thing that
would make it quiet again is an exemption list nobody notices growing. The
success line prints its size, and the cases below hold that line and the failure
it prints for a tenth.

All three passes are asserted here, and the first one — resolution — is the pass
the whole check rests on: `citations()` decides what counts as a claim about the
tree, and everything after it judges only what that reading found. A `citations()`
that has stopped recognising a backticked path reports `OK: all 0 .swift paths…`
and exits 0, which is the "green because it never ran" answer in miniature, and
the preflight would carry it through to a build. So what it reads, where a read
path may resolve, which line each offender is reported on, and the flags
(`--quiet`, `--map`, `--config`) that decide what a run prints and what it reads,
are held here beside the reachability cases.

Every case runs the real script over a throwaway checkout: a `quality.json` at
its root naming the map, the app target, the roots and the exemptions, an app
target of a handful of Swift files, and a map that cites them. The script is
handed that file with `--config`, so it judges the fixture and nothing here can
be answered by the state of this checkout — except the one case that asks for
exactly that, which runs the script from this checkout's root with no `--config`,
so it finds the `quality.json` there the way a preflight run does, and checks
the counts it prints against the tree.

It starts no build, reads no process list and writes nothing outside a temporary
directory, so it is safe to run — on its own or as part of the suite — while
Kiteloop is running.

  python3 quality/tests/test-check-features-map.py
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True  # leave no __pycache__ beside the script

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BIN = os.path.join(os.path.dirname(HERE), "bin")
SCRIPT = os.path.join(BIN, "check-features-map.py")
CONFIG = os.path.join(REPO, "quality.json")

sys.path.insert(0, BIN)
import quality_config  # noqa: E402

failed = 0
fixtures = []


def check(name, ok, detail=""):
    global failed
    if ok:
        print("  ok    %s" % name)
    else:
        print("  FAIL  %s%s" % (name, "\n          " + detail if detail else ""))
        failed += 1


def check_equal(name, expected, actual):
    check(name, expected == actual, "expected %r, got %r" % (expected, actual))


def check_contains(name, needle, haystack):
    check(name, needle in haystack, "%r not in:\n%s" % (needle, haystack))


def check_absent(name, needle, haystack):
    check(name, needle not in haystack, "%r unexpectedly in:\n%s" % (needle, haystack))


def load_script():
    """The script as a module, for reading the functions a case is about."""
    spec = importlib.util.spec_from_file_location("check_features_map", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)


# --- The fixture --------------------------------------------------------------
#
# One wired service the map marks `[x]`, one wired service it says nothing about,
# and one that is unreached and on the exemption list -- in the app target, and
# the same shape once more in the package, since the pass sweeps both. That is
# the green shape every case starts from, so a case says what it changes and
# nothing else.
#
# `Wired.swift` declares `WiredService`: the file name and the type name differ on
# purpose, because the reading is of declared names and a filename match would
# call `Services/ItemDossier.swift` unreachable for declaring `ItemDisagreements`.
WIRED = "struct WiredService {\n    func run() {}\n}\n"
REACHED = "final class ReachedStore {\n    func load() {}\n}\n"
PKG_WIRED = "struct PkgWiredService {\n    func run() {}\n}\n"
MOBILE_WIRED = "struct MobileWiredService {\n    func run() {}\n}\n"

# The fixtures' own exempt service, written into the fixture's quality.json
# rather than borrowed from the list that ships.
#
# It used to be a real entry, on the reasoning that the case should be about the
# list that ships. That coupled every fixture here to which services happened to
# be exempt in the real tree — and when the last of the nine was retired out of
# it, these cases failed for a reason that had nothing to do with what they
# assert. What the fixtures need is *an* exemption behaving like one; whether the
# shipping list is empty is a different question, asked against this checkout at
# the bottom of this file.
EXEMPT_SERVICE = "Dormant.swift"
EXEMPT_DECLARES = "Dormancy, DormantService"
EXEMPT_SOURCE = "protocol Dormancy {}\n\nstruct DormantService: Dormancy {}\n"

# The fixture's layout, spelled the way the real quality.json spells this repo's:
# the app target, the two test targets, the package's sources and tests, and
# the companion's source root. `"service_roots"` sweeps all three sources --
# the three `SERVICE_ROOTS` names below.
APP_ROOT = "Kiteloop/Kiteloop"
PKG_ROOT = "Kiteloop/KiteloopCore/Sources/KiteloopCore"
MOBILE_ROOT = "KiteloopMobile/Sources"
SERVICE_ROOTS = [APP_ROOT, PKG_ROOT, MOBILE_ROOT]
ROOTS = [
    APP_ROOT,
    "Kiteloop/KiteloopTests",
    "Kiteloop/KiteloopUITests",
    PKG_ROOT,
    "Kiteloop/KiteloopCore/Tests/KiteloopCoreTests",
]

# `"exempt_services"` is keyed repo-relative -- a path under the app target and
# one under the package could otherwise share a `Services/` filename and
# collide in the list.
EXEMPT = {os.path.join(APP_ROOT, "Services", EXEMPT_SERVICE): "a fixture-only exemption"}

APP = (
    "@main\nstruct KiteloopApp {\n"
    "    let wired = WiredService()\n"
    "    let store = ReachedStore()\n"
    "}\n"
)

# The package's own entry point, read the same way `APP` is: a source at the
# package root naming what it constructs. A package service reached only from
# here -- never from the app target -- is what the widened sweep exists to see.
PKG = "struct PackageRoot {\n    let wired = PkgWiredService()\n}\n"

# The companion's own entry point, read the same way `APP` and `PKG` are: a
# source under the companion's root naming what it constructs. A companion
# service reached only from here is what this item's widened sweep exists to
# see.
MOBILE = "struct MobileRoot {\n    let wired = MobileWiredService()\n}\n"

MAP = """# Features

| done | capability | source | tests |
| --- | --- | --- | --- |
| [x] | A wired thing | `Services/Wired.swift` | `WiredTests.swift` |
"""


def fixture(
    services=None,
    pkg_services=None,
    mobile_services=None,
    app=APP,
    pkg=PKG,
    mobile=MOBILE,
    features=MAP,
    tests=None,
    exempt=None,
):
    """A throwaway checkout, with the quality.json the script is handed."""
    root = tempfile.mkdtemp(prefix="test-check-features-map-")
    fixtures.append(root)

    write(os.path.join(root, "quality.json"), json.dumps({
        "features_map": {
            "file": "docs/features.md",
            "roots": ROOTS,
            "service_roots": SERVICE_ROOTS,
            "exempt_services": EXEMPT if exempt is None else exempt,
        }
    }, indent=2))

    app_root = os.path.join(root, APP_ROOT)
    write(os.path.join(app_root, "KiteloopApp.swift"), app)
    sources = {
        "Wired.swift": WIRED,
        "Reached.swift": REACHED,
        EXEMPT_SERVICE: EXEMPT_SOURCE,
    }
    sources.update(services or {})
    for name, text in sources.items():
        write(os.path.join(app_root, "Services", name), text)

    pkg_root = os.path.join(root, PKG_ROOT)
    write(os.path.join(pkg_root, "PackageRoot.swift"), pkg)
    pkg_sources = {"PkgWired.swift": PKG_WIRED}
    pkg_sources.update(pkg_services or {})
    for name, text in pkg_sources.items():
        write(os.path.join(pkg_root, "Services", name), text)

    mobile_root = os.path.join(root, MOBILE_ROOT)
    write(os.path.join(mobile_root, "MobileRoot.swift"), mobile)
    mobile_sources = {"MobileWired.swift": MOBILE_WIRED}
    mobile_sources.update(mobile_services or {})
    for name, text in mobile_sources.items():
        write(os.path.join(mobile_root, "Services", name), text)

    test_sources = {"WiredTests.swift": "final class WiredTests {}\n"}
    test_sources.update(tests or {})
    for name, text in test_sources.items():
        write(os.path.join(root, "Kiteloop", "KiteloopTests", name), text)

    write(os.path.join(root, "docs", "features.md"), features)
    return root


def checked_line(app_count, pkg_count, mobile_count, exempt_count):
    """The tail of the OK line's third clause, for the fixture's three swept roots."""
    return (
        f"so is each of {app_count} services under {APP_ROOT}/Services/, "
        f"{pkg_count} services under {PKG_ROOT}/Services/ and "
        f"{mobile_count} services under {MOBILE_ROOT}/Services/ ({exempt_count} exempt)"
    )


def run(root, *args):
    """Run the script under the quality.json in `root`."""
    proc = subprocess.run(
        [sys.executable, SCRIPT, "--config", os.path.join(root, "quality.json"), *args],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def settings_for(root, map_path=None):
    """The script's own reading of the quality.json in `root`."""
    return module.Settings(quality_config.Config(os.path.join(root, "quality.json")), map_path)


print("test-check-features-map")

# The script's own functions, for the cases that read one directly rather than
# through a run. Loaded once, from the real script: what `citations` recognises
# is a property of the thing that ships.
module = load_script()

# --- The green shape ----------------------------------------------------------
#
# Three services, one of them unreached and exempt: the run passes, and the line
# it prints says how many it judged and how many it did not. That count is the
# whole of what keeps the exemption list from going quiet, so it is asserted as
# text rather than inferred from the exit status.

code, out, err = run(fixture())
check_equal("a tree whose services are all reached or exempt passes", 0, code)
check_contains(
    "the OK line says how many services were checked under each swept root and how many are exempt",
    checked_line(2, 1, 1, 1),
    out,
)
check_equal("a passing run says nothing on stderr", "", err)

code, out, err = run(fixture(), "--quiet")
check_equal("--quiet prints nothing when everything passes", ("", "", 0), (out, err, code))

# --- A service nothing reaches ------------------------------------------------
#
# The failure this pass exists for: a file added under Services/ that no other app
# source names. It is reported by path and by the types it declares — the two
# things a reader needs to go and look, since neither the file name nor the map
# will tell them — and by path alone, because unlike a row there is no line for it
# to sit on.

ORPHAN = (
    "enum OrphanKind {\n    case first\n}\n\n"
    "struct OrphanService {\n"
    "    let kind: OrphanKind\n"
    "    func run() {}\n"
    "}\n"
)
code, out, err = run(fixture({"Orphan.swift": ORPHAN}))
check_equal("a service no app source names fails the check", 1, code)
check_contains(
    "the failure names the file and the types it declares",
    "Kiteloop/Kiteloop/Services/Orphan.swift (declares OrphanKind, OrphanService)",
    err,
)
check_contains(
    "the failure says what an unreachable service means",
    "are named by no other swept source",
    err,
)
check_contains(
    "the failure says where an exemption goes, for a service that must stay dormant",
    'add it to "exempt_services" in quality.json',
    err,
)
check_absent(
    "the offender is named by path alone — there is no row for it to sit on",
    "Services/Orphan.swift:",
    err,
)
check_absent(
    "an exempt service is not named beside it",
    EXEMPT_SERVICE,
    err,
)
check_equal("the failure is on stderr, and stdout carries none of it", "", out)

code, out, err = run(fixture({"Orphan.swift": ORPHAN}), "--quiet")
check_equal("--quiet still fails", 1, code)
check_contains("--quiet still names the offender", "Services/Orphan.swift", err)

# A service that names only itself is unreached: the type it declares is
# mentioned in its own file and nowhere else, which is what `- {path}` in the
# reading is for. Without that a service is reachable as soon as it constructs
# itself, which every one of them does.
SELF_ONLY = (
    "struct LonelyService {\n"
    "    static let shared = LonelyService()\n"
    "}\n"
)
code, out, err = run(fixture({"Lonely.swift": SELF_ONLY}))
check_equal("a service only its own file names is unreached", 1, code)
check_contains(
    "the self-naming service is the one reported",
    "Kiteloop/Kiteloop/Services/Lonely.swift (declares LonelyService)",
    err,
)

# A file that declares no top-level type offers no name to look for, so nothing
# can be concluded about it — the same rule the `[x]` pass already followed.
EXTENSION_ONLY = "extension String {\n    var trimmedTwice: String { self }\n}\n"
code, out, err = run(fixture({"Extras.swift": EXTENSION_ONLY}))
check_equal("a service declaring no top-level type is not reported", 0, code)
check_contains(
    "and is judged rather than skipped — it is in the checked count",
    checked_line(3, 1, 1, 1),
    out,
)

# A service under a subdirectory of Services/ is judged the same as one at the
# top level -- the reachability pass reads Services/ recursively, not just its
# immediate contents.
SUBDIR_ORPHAN = (
    "enum SubOrphanKind {\n    case first\n}\n\n"
    "struct SubOrphanService {\n"
    "    let kind: SubOrphanKind\n"
    "}\n"
)
code, out, err = run(fixture({"Control/Orphan.swift": SUBDIR_ORPHAN}))
check_equal("a service under a Services/ subdirectory no app source names fails", 1, code)
check_contains(
    "the failure names the file by its subdirectory path",
    "Kiteloop/Kiteloop/Services/Control/Orphan.swift "
    "(declares SubOrphanKind, SubOrphanService)",
    err,
)

# The sweep reaches past the app target: a service under the package's own
# Services/ that no app or package source names is caught the same way. This is
# the gap the widened sweep exists to close -- before it, nothing walked
# `Kiteloop/KiteloopCore/Sources/KiteloopCore/Services/` at all.
PKG_ORPHAN = (
    "enum PkgOrphanKind {\n    case first\n}\n\n"
    "struct PkgOrphanService {\n"
    "    let kind: PkgOrphanKind\n"
    "}\n"
)
code, out, err = run(fixture(pkg_services={"PkgOrphan.swift": PKG_ORPHAN}))
check_equal("a package service no app or package source names fails the check", 1, code)
check_contains(
    "the failure names the file under the package root and the types it declares",
    "Kiteloop/KiteloopCore/Sources/KiteloopCore/Services/PkgOrphan.swift "
    "(declares PkgOrphanKind, PkgOrphanService)",
    err,
)

# The sweep reaches the companion too: a service under KiteloopMobile/Sources'
# own Services/ that no source names is caught the same way. This is the gap
# this item's widened sweep exists to close -- before it, nothing walked
# `KiteloopMobile/Sources/Services/` at all.
MOBILE_ORPHAN = (
    "enum MobileOrphanKind {\n    case first\n}\n\n"
    "struct MobileOrphanService {\n"
    "    let kind: MobileOrphanKind\n"
    "}\n"
)
code, out, err = run(fixture(mobile_services={"MobileOrphan.swift": MOBILE_ORPHAN}))
check_equal("a companion service no source names fails the check", 1, code)
check_contains(
    "the failure names the file under the companion root and the types it declares",
    "KiteloopMobile/Sources/Services/MobileOrphan.swift "
    "(declares MobileOrphanKind, MobileOrphanService)",
    err,
)

# And a companion service that *is* named -- by the fixture's own entry point
# -- passes, and is counted among the checked services, the same as an app or
# package one: the wiring, not the root, is what decides reachability.
code, out, err = run(fixture())
check_equal("a companion service named by another source passes", 0, code)
check_contains(
    "and is counted among the checked services",
    checked_line(2, 1, 1, 1),
    out,
)

# `package` is a declaration modifier too -- every service under the real
# package root declares with it (`package struct`, `package final class`), and
# a `DECLARATION_RE` that does not recognise it reads no name at all for a file
# where every top-level declaration carries it, so `declared_names` returns
# nothing and `unreached_among` skips the file rather than reporting it.
PKG_MODIFIER_ORPHAN = (
    "package enum PkgModifierOrphanKind {\n    case first\n}\n\n"
    "package struct PkgModifierOrphanService {\n"
    "    let kind: PkgModifierOrphanKind\n"
    "}\n"
)
code, out, err = run(fixture(pkg_services={"PkgModifierOrphan.swift": PKG_MODIFIER_ORPHAN}))
check_equal("a package-modifier service no other source names fails the check", 1, code)
check_contains(
    "the failure names the file and the types its package declaration declares",
    "Kiteloop/KiteloopCore/Sources/KiteloopCore/Services/PkgModifierOrphan.swift "
    "(declares PkgModifierOrphanKind, PkgModifierOrphanService)",
    err,
)

# --- A service that is only talked about --------------------------------------
#
# Prose is not construction. This repo's doc comments name other services
# constantly — the header of `Services/SubprocessOutput.swift` names four it does
# not call — so a name counted wherever it appears makes a dormant service
# reachable the moment any file merely mentions it, which is the state both
# reachability passes exist to catch. Same service in each case below; what
# changes is whether the file that names it does so in code or about it.

TALKED_ABOUT = "struct TalkedAboutService {\n    func run() {}\n}\n"
TALKED_ABOUT_REPORT = (
    "Kiteloop/Kiteloop/Services/TalkedAbout.swift (declares TalkedAboutService)"
)

code, out, err = run(fixture(
    {"TalkedAbout.swift": TALKED_ABOUT},
    app=APP + "\n// TalkedAboutService is next; nothing constructs it yet.\n",
))
check_equal("a service named only in a // comment is unreached", 1, code)
check_contains(
    "the comment-only service is reported by path and by what it declares",
    TALKED_ABOUT_REPORT,
    err,
)

code, out, err = run(fixture(
    {"TalkedAbout.swift": TALKED_ABOUT},
    app=APP + "\n/*\n TalkedAboutService is next; nothing constructs it yet.\n*/\n",
))
check_equal("a service named only inside a /* */ block is unreached", 1, code)
check_contains(
    "the block-comment-only service is reported the same way",
    TALKED_ABOUT_REPORT,
    err,
)

# The other half of the rule: a name in code still reaches, including on a line
# whose tail is a comment. Cutting the line at `//` must not take the code with
# it.
TRAILING_COMMENT = (
    "@main\nstruct KiteloopApp {\n"
    "    let wired = WiredService()\n"
    "    let store = ReachedStore()\n"
    "    let talked = TalkedAboutService()  // and said so, which changes nothing\n"
    "}\n"
)
code, out, err = run(fixture({"TalkedAbout.swift": TALKED_ABOUT}, app=TRAILING_COMMENT))
check_equal("a service named in code passes, trailing comment or not", 0, code)
check_contains(
    "and the run judged it rather than skipping it",
    checked_line(3, 1, 1, 1),
    out,
)

# A `//` inside a string literal is not the start of a comment. Cutting there
# would delete the rest of the line's real code and report a service the app does
# construct as an orphan.
STRING_WITH_SLASHES = (
    "@main\nstruct KiteloopApp {\n"
    "    let wired = WiredService()\n"
    "    let store = ReachedStore()\n"
    '    let docs = "https://example.com"; let talked = TalkedAboutService()\n'
    "}\n"
)
code, out, err = run(fixture({"TalkedAbout.swift": TALKED_ABOUT}, app=STRING_WITH_SLASHES))
check_equal(
    "a `//` inside a string literal does not hide the code after it",
    0,
    code,
)
check_absent(
    "so the service constructed on that line is not reported",
    "Services/TalkedAbout.swift",
    err,
)

# --- One mention index, read across both roots ---------------------------------
#
# The reachability reading is built once from every source the sweep found, app
# target and package together, so a service in either root is reached by a
# mention from the other. Before the sweep covered the package this was moot --
# nothing under the package's Services/ was judged at all -- so this is the case
# that would have caught the boundary the widened sweep exists to close.

APP_CALLS_PKG = (
    "@main\nstruct KiteloopApp {\n"
    "    let wired = WiredService()\n"
    "    let store = ReachedStore()\n"
    "    let cross = CrossFromAppService()\n"
    "}\n"
)
CROSS_FROM_APP = "struct CrossFromAppService {\n    func run() {}\n}\n"
code, out, err = run(fixture(pkg_services={"CrossFromApp.swift": CROSS_FROM_APP}, app=APP_CALLS_PKG))
check_equal("a package service constructed only from app code is reached", 0, code)
check_absent("so it is not reported unreached", "CrossFromApp.swift", err)

CROSS_FROM_PKG_TARGET = "struct CrossFromPkgTarget {\n    func run() {}\n}\n"
PKG_CALLS_APP = PKG + "\nstruct PkgCallsAppService {\n    let target = CrossFromPkgTarget()\n}\n"
code, out, err = run(fixture({"CrossFromPkg.swift": CROSS_FROM_PKG_TARGET}, pkg=PKG_CALLS_APP))
check_equal("an app service constructed only from package code is reached", 0, code)
check_absent("so it is not reported unreached", "CrossFromPkg.swift", err)

# --- The exemption list -------------------------------------------------------
#
# The green shape above already carries an exempt service; this says the pass
# would have caught it, which is the only thing that makes the exemption mean
# anything. Same tree, same content, the one entry taken out of the list.

code, out, err = run(fixture(exempt={}))
check_equal("the same service fails once it is off the exemption list", 1, code)
check_contains(
    "which is what the exemption was suppressing",
    f"Kiteloop/Kiteloop/Services/{EXEMPT_SERVICE} (declares {EXEMPT_DECLARES})",
    err,
)

# An "exempt_services" entry that names no file under any swept root's
# Services/ at all -- the tree moved past it rather than someone taking it off
# the list. Left unreported, it would go on silently exempting whatever service
# a later commit happens to add at that same path.
code, out, err = run(fixture(exempt={
    **EXEMPT,
    os.path.join(APP_ROOT, "Services", "Ghost.swift"): "a stale reason nobody deleted",
}))
check_equal(
    "an exempt_services entry naming a service the tree does not have fails",
    1,
    code,
)
check_contains(
    "the failure names the entry's path",
    "Kiteloop/Kiteloop/Services/Ghost.swift",
    err,
)
check_contains(
    "the failure prints the reason recorded beside the entry",
    "a stale reason nobody deleted",
    err,
)
check_contains(
    "the failure names the file the entry lives in",
    '"exempt_services" entry(ies) in quality.json',
    err,
)
check_contains(
    "the failure says to delete the entry rather than add a file",
    "Delete the entry",
    err,
)
check_absent(
    "a still-valid exemption is not reported alongside the stale one",
    f"Services/{EXEMPT_SERVICE}: ",
    err,
)

# --- An exemption for a service reached again ----------------------------------
#
# An exemption is a claim that nothing reaches the service. Unlike `checked`,
# the reachability pass never looks at an exempt service again to see whether
# that is still true -- so once something does reach it, the exemption stands
# forever, still counted in the success line's exempt figure, and would
# silently exempt the service again if it later went dormant. This is the case
# the fourth check exists to catch.

REACHED_EXEMPT_APP = (
    "@main\nstruct KiteloopApp {\n"
    "    let wired = WiredService()\n"
    "    let store = ReachedStore()\n"
    "    let dormant = DormantService()\n"
    "}\n"
)
code, out, err = run(fixture(app=REACHED_EXEMPT_APP))
check_equal("an exempt service another swept source now names fails", 1, code)
check_contains(
    "the failure names the entry's path and the reason recorded for it",
    f"Kiteloop/Kiteloop/Services/{EXEMPT_SERVICE}: a fixture-only exemption",
    err,
)
check_contains(
    "and the types it declares",
    f"declares {EXEMPT_DECLARES}",
    err,
)
check_contains(
    "and what now reaches it",
    "reached by Kiteloop/Kiteloop/KiteloopApp.swift",
    err,
)
check_contains(
    "the failure says an exemption stands unless something retires it",
    'entry(ies) in quality.json name a service',
    err,
)
check_contains(
    "and says to delete the entry now the service is wired in",
    "Delete the entry -- the service is wired in now",
    err,
)
check_equal("the failure is on stderr, and stdout carries none of it", "", out)

# A comment mentioning the exempt service does not retire it -- the reading
# strips comments first, exactly as the orphan pass does.
COMMENTED_EXEMPT_APP = APP + f"\n// {EXEMPT_DECLARES.split(', ')[1]} is next; nothing constructs it yet.\n"
code, out, err = run(fixture(app=COMMENTED_EXEMPT_APP))
check_equal("an exempt service named only in a comment stays exempt", 0, code)

# A file declaring no top-level type offers no name to look for, so nothing can
# be concluded about it -- the same rule `unreached_among` already follows, and
# this pass reads through the same function.
EXTENSION_EXEMPT_SERVICE = "DormantExtension.swift"
extension_exempt = {
    **EXEMPT,
    os.path.join(APP_ROOT, "Services", EXTENSION_EXEMPT_SERVICE): "a fixture-only exemption",
}
code, out, err = run(fixture(
    services={EXTENSION_EXEMPT_SERVICE: EXTENSION_ONLY},
    exempt=extension_exempt,
))
check_equal("an exempt service declaring no top-level type is not reported", 0, code)
check_absent(
    "so it never appears in the failure list",
    EXTENSION_EXEMPT_SERVICE,
    err,
)

# Two exempt services naming only each other have not been wired into a path
# the app runs -- they are still the same dormant cluster, so neither retires
# the other's exemption. Excluded here is what `restricted_to` keeps a mention
# from an exempt source out of: this is the case that requires it, since both
# ClusterA and ClusterB would otherwise "reach" each other through the shared
# index the orphan pass builds.
CLUSTER_A_SERVICE = "ClusterA.swift"
CLUSTER_B_SERVICE = "ClusterB.swift"
CLUSTER_A_SOURCE = "struct ClusterAService {\n    let b = ClusterBService()\n}\n"
CLUSTER_B_SOURCE = "struct ClusterBService {\n    let a = ClusterAService()\n}\n"
cluster_exempt = {
    **EXEMPT,
    os.path.join(APP_ROOT, "Services", CLUSTER_A_SERVICE): "a fixture-only exemption",
    os.path.join(APP_ROOT, "Services", CLUSTER_B_SERVICE): "a fixture-only exemption",
}
code, out, err = run(fixture(
    services={CLUSTER_A_SERVICE: CLUSTER_A_SOURCE, CLUSTER_B_SERVICE: CLUSTER_B_SOURCE},
    exempt=cluster_exempt,
))
check_equal("two exempt services naming only each other both stay exempt", 0, code)
check_absent("neither is reported as retired", "ClusterAService", err)
check_absent("neither is reported as retired", "ClusterBService", err)

# --- What the map cites -------------------------------------------------------
#
# `citations()` is the reading every later pass is bounded by: a path it does not
# recognise is a claim about the tree nobody ever checks, and a reading that
# recognises nothing reports `OK: all 0 .swift paths…` and exits 0 — a check that
# has stopped checking, saying so in a sentence that scans as success. So what it
# takes for a citation, and which line each is reported on, are asserted directly.
# The cases below drive the same function through real runs; these say what it
# does with map text that would take a fixture apiece to arrange.

check_equal(
    "a citation is read with the line it sits on",
    [("Services/Wired.swift", 1)],
    module.citations("| [x] | A thing | `Services/Wired.swift` | — |\n"),
)
check_equal(
    "two citations on one line are read in the order they appear",
    [("A.swift", 2), ("B.swift", 2)],
    module.citations("\n`A.swift` and `B.swift`\n"),
)
# One row is one claim, so a path two rows cite is two of them: a reader fixing
# the row they were sent to still has the other one to fix.
check_equal(
    "one path cited on two lines is read once per line",
    [("Services/Gone.swift", 1), ("Services/Gone.swift", 2)],
    module.citations("`Services/Gone.swift`\n`Services/Gone.swift`\n"),
)
# Backticks are how the map presents a path, and prose about a file is not a
# claim that it exists — this file's own headers name renamed and deleted sources
# on purpose, and a reading that counted those would fail the check for saying so.
check_equal(
    "a .swift path outside backticks is not a citation",
    [],
    module.citations("Services/Gone.swift was renamed; see the row above.\n"),
)
check_equal(
    "a backticked token that is not a .swift path is not a citation",
    [],
    module.citations("`docs/features.md` and `quality/bin/check-features-map.py`\n"),
)
# A span is closed on its own line or not at all, so an unclosed backtick cannot
# reach forward and swallow the next line's citation.
check_equal(
    "an unclosed backtick does not consume the citation on the line below",
    [("B.swift", 2)],
    module.citations("`unclosed\n`B.swift`\n"),
)
check_equal(
    "a citation padded inside its backticks is read without the padding",
    [("Services/Wired.swift", 1)],
    module.citations("` Services/Wired.swift`\n"),
)

# --- Where a cited path may resolve -------------------------------------------
#
# A Source path is written relative to the app target and a Test path relative to
# its bundle, so the check tries every root rather than trusting the column a path
# sits in. Every root is exercised, driven off the list the fixture's quality.json
# carries rather than spelled out per case, so a root added to it is covered
# without anyone remembering to.

PLACED = "struct Placed%d {}\n"
PLACED_ROWS = "".join(
    "| [ ] | Placed under root %d | `Placed%d.swift` | — |\n" % (i, i)
    for i, _ in enumerate(ROOTS)
)


def placed_fixture(skip=None):
    """The green fixture, plus one cited file under each root but `skip`."""
    root = fixture(features=MAP + PLACED_ROWS)
    for i, cited_root in enumerate(ROOTS):
        if i != skip:
            write(os.path.join(root, cited_root, "Placed%d.swift" % i), PLACED % i)
    return root


code, out, err = run(placed_fixture())
check_equal("a path under any of the roots resolves", 0, code)
check_contains(
    "and the OK line counts every citation and names the roots it tried",
    "OK: all %d .swift paths cited by docs/features.md exist under %s;"
    % (2 + len(ROOTS), ", ".join("%s/" % root for root in ROOTS)),
    out,
)

# One root at a time, because a pass that tried only the first would satisfy the
# case above for the file that happens to live there.
for index, root in enumerate(ROOTS):
    code, out, err = run(placed_fixture(skip=index))
    check_equal("a path missing from %s/ is not resolved by the other roots" % root, 1, code)
    check_contains(
        "and is reported by the row that cites it",
        "docs/features.md:%d: Placed%d.swift" % (6 + index, index),
        err,
    )
    check_contains("with only that one named", "FAIL: 1 .swift path(s)", err)

# The roots are the scope, not the checkout: a .swift file that exists in the tree
# but under none of them is not a path the map may cite.
strays = placed_fixture()
write(os.path.join(strays, "Kiteloop", "Stray.swift"), "struct Stray {}\n")
check(
    "a .swift file in the tree but under no root does not resolve",
    not module.resolves("Stray.swift", settings_for(strays)),
)
check(
    "a path under none of the roots resolves nowhere",
    not module.resolves("Nowhere.swift", settings_for(strays)),
)

# The roots are read from the file, not from anywhere in the script: a
# quality.json listing one root resolves under that one and no other.
narrow = fixture(features=MAP + "| [ ] | Elsewhere | `Elsewhere.swift` | — |\n")
write(os.path.join(narrow, "Kiteloop", "KiteloopTests", "Elsewhere.swift"), "struct Elsewhere {}\n")
with open(os.path.join(narrow, "quality.json")) as handle:
    narrowed = json.load(handle)
narrowed["features_map"]["roots"] = [APP_ROOT]
write(os.path.join(narrow, "quality.json"), json.dumps(narrowed))
code, out, err = run(narrow)
check_equal("a root the quality.json does not list is not tried", 1, code)
check_contains("and the test cited there resolves nowhere", "FAIL: 2 .swift path(s)", err)
check_contains("the test the [x] row cites among them", "docs/features.md:5: WiredTests.swift", err)

# --- A path that resolves nowhere ---------------------------------------------
#
# The failure the first pass exists for: a row whose file was renamed or deleted,
# which goes on reading as coverage somebody checked because nothing re-reads the
# path. Eleven of the map's paths were in that state before this check existed.

GONE_MAP = MAP + "| [ ] | A gone thing | `Services/Gone.swift` | `GoneTests.swift` |\n"
code, out, err = run(fixture(features=GONE_MAP))
check_equal("a cited path that resolves nowhere fails", 1, code)
check_contains(
    "and is reported with the line of the row that cites it",
    "docs/features.md:6: Services/Gone.swift",
    err,
)
check_contains("the resolution failure says what it means", "exist nowhere", err)
check_equal("the resolution failure is on stderr alone", "", out)

code, out, err = run(fixture(features=GONE_MAP), "--quiet")
check_equal("--quiet still fails on a path that resolves nowhere", 1, code)
check_contains(
    "--quiet still names the offender and its line",
    "docs/features.md:6: Services/Gone.swift",
    err,
)

# The same file cited by two rows is two claims, and the reader fixing one of them
# needs to be told about the other — so it is reported once per line rather than
# once per path.
DUPLICATE_MAP = MAP + (
    "| [ ] | A gone thing | `Services/Gone.swift` | — |\n"
    "| [ ] | The same file, another row | `Services/Gone.swift` | — |\n"
)
code, out, err = run(fixture(features=DUPLICATE_MAP))
check_equal("one missing path cited twice fails", 1, code)
check_contains("it is counted once per row", "FAIL: 2 .swift path(s)", err)
check_contains("the first row is named", "docs/features.md:6: Services/Gone.swift", err)
check_contains("and so is the second", "docs/features.md:7: Services/Gone.swift", err)

# Prose is not a citation, through a run as well as through the reading above: the
# map's own text names files that are gone, and a check that failed for saying so
# would be unusable on the file it is pointed at.
PROSE_MAP = MAP + "\nServices/Gone.swift was renamed; the row above is current.\n"
code, out, err = run(fixture(features=PROSE_MAP))
check_equal("a .swift mention outside backticks is not read as a citation", 0, code)
check_contains(
    "so it is not counted either",
    "OK: all 2 .swift paths cited by docs/features.md",
    out,
)

# --- Which map is read --------------------------------------------------------
#
# `--map` names the file to check, over whatever the quality.json names. The
# fixture's own docs/features.md is broken in the case below and the handed one
# is not, so a run that passes can only have read the file it was given — a
# `--map` quietly falling back to the configured one would report that file's
# failure and be caught here.

swapped = fixture(features=GONE_MAP)
write(os.path.join(swapped, "docs", "draft-map.md"), MAP)
code, out, err = run(swapped, "--map", os.path.join(swapped, "docs", "draft-map.md"))
check_equal("--map reads the file it is handed, not the configured map", 0, code)
check_contains(
    "and names it in the OK line",
    "OK: all 2 .swift paths cited by docs/draft-map.md",
    out,
)
check_absent("the configured map is not read at all", "features.md", out)

code, out, err = run(swapped)
check_equal("without --map the configured map is read, and it is the broken one", 1, code)
check_contains("named as docs/features.md", "docs/features.md:6:", err)

# And the configured map is the file quality.json names, not a spelling in the
# script: the same broken tree passes once "file" points at the sound map.
with open(os.path.join(swapped, "quality.json")) as handle:
    repointed = json.load(handle)
repointed["features_map"]["file"] = "docs/draft-map.md"
write(os.path.join(swapped, "quality.json"), json.dumps(repointed))
code, out, err = run(swapped)
check_equal('"file" in quality.json decides which map a bare run reads', 0, code)
check_contains(
    "and that map is the one the OK line names",
    "OK: all 2 .swift paths cited by docs/draft-map.md",
    out,
)

# A map handed in from outside the checkout is named by its absolute path rather
# than by the run of `../` a relative one would be, which is what `display` is
# for: the point of the name is that a reader can go and open it.
outside = tempfile.mkdtemp(prefix="test-check-features-map-outside-")
fixtures.append(outside)
outside_map = os.path.join(outside, "elsewhere.md")
write(outside_map, GONE_MAP)
code, out, err = run(fixture(), "--map", outside_map)
check_equal("a map outside the checkout is read", 1, code)
check_contains(
    "and named by its absolute path",
    "%s:6: Services/Gone.swift" % outside_map,
    err,
)
check_absent("rather than by a run of ../", os.pardir + os.sep, err)

# The same two branches read directly, against a fixture's own root.
inside = fixture()
check_equal(
    "display names a map inside the checkout repo-relatively",
    os.path.join("docs", "features.md"),
    module.display(settings_for(inside).features, inside),
)
check_equal(
    "and one outside it by the path it was given",
    os.path.join(outside, "features.md"),
    module.display(os.path.join(outside, "features.md"), inside),
)

# --- Which quality.json is read -----------------------------------------------
#
# Without `--config` the script walks up from the working directory, which is how
# a preflight run from the repo root or from Kiteloop/ finds the same file. A
# missing file, or one without the section this check reads, is an error that
# names what is missing — not a default that lets the run go green on nothing.

walked = fixture()
proc = subprocess.run(
    [sys.executable, SCRIPT],
    cwd=os.path.join(walked, "Kiteloop", "Kiteloop", "Services"),
    capture_output=True,
    text=True,
)
check_equal("without --config the nearest quality.json above the cwd is read", 0, proc.returncode)
check_contains(
    "and the run is the fixture's, not this checkout's",
    checked_line(2, 1, 1, 1),
    proc.stdout,
)

nowhere = tempfile.mkdtemp(prefix="test-check-features-map-noconfig-")
fixtures.append(nowhere)
proc = subprocess.run(
    [sys.executable, SCRIPT], cwd=nowhere, capture_output=True, text=True,
)
check_equal("with no quality.json anywhere above the cwd the run exits 2", 2, proc.returncode)
check_contains("and says which file it was looking for", "no quality.json", proc.stderr)

sectionless = fixture()
write(os.path.join(sectionless, "quality.json"), json.dumps({"project": "Fixture"}))
code, out, err = run(sectionless)
check_equal("a quality.json without a features_map section exits 2", 2, code)
check_contains("and names the section it lacks", '"features_map"', err)

keyless = fixture()
write(os.path.join(keyless, "quality.json"), json.dumps({"features_map": {"file": "docs/features.md"}}))
code, out, err = run(keyless)
check_equal("a features_map section missing a key exits 2", 2, code)
check_contains("and names the key", '"roots"', err)

# The key the third pass depends on: a config that has everything else but
# "service_roots" fails naming it, rather than falling back to sweeping the
# citation-resolution roots or nothing at all.
roots_only = fixture()
with open(os.path.join(roots_only, "quality.json")) as handle:
    without_service_roots = json.load(handle)
del without_service_roots["features_map"]["service_roots"]
write(os.path.join(roots_only, "quality.json"), json.dumps(without_service_roots))
code, out, err = run(roots_only)
check_equal("a features_map section missing service_roots exits 2", 2, code)
check_contains("and names service_roots rather than defaulting to the app root", '"service_roots"', err)

# --- The [x] rows -------------------------------------------------------------
#
# The second pass: a path resolving is not the same as the capability existing.
# It shares the reachability reading with the third pass and the run with both,
# so these hold the mark itself — which rows are candidates, and what an exemption
# does not excuse.

# An `[x]` row citing a service nothing reaches. The service is on the exemption
# list, which the row pass does not consult: an exemption freezes a service the
# map says nothing about, while an `[x]` is somebody writing down that the
# capability is finished, and that claim is wrong either way.
CLAIMED_MAP = MAP + (
    f"| [x] | An exported thing | `Services/{EXEMPT_SERVICE}` | `WiredTests.swift` |\n"
)
code, out, err = run(fixture(features=CLAIMED_MAP))
check_equal("an [x] row citing an unreached service still fails", 1, code)
check_contains(
    "and is still reported by row, exemption list or not",
    f"docs/features.md:6: Services/{EXEMPT_SERVICE} (declares {EXEMPT_DECLARES})",
    err,
)
check_contains(
    "the [x] failure still says what the mark claimed",
    "A capability nothing constructs is not implemented",
    err,
)

# A row that does not claim to be finished is not a candidate, whatever it cites.
UNCLAIMED_MAP = MAP + (
    f"| [~] | An exported thing | `Services/{EXEMPT_SERVICE}` | `WiredTests.swift` |\n"
)
code, out, err = run(fixture(features=UNCLAIMED_MAP))
check_equal("a [~] row citing an unreached service still passes", 0, code)

# The candidate filter reaches past the app target too: before the widened
# sweep, resolving a row's citation only under `app_dir` silently dropped every
# package citation from this pass, whatever its status cell said. An [x] row
# citing an unreached package service is judged the same as an app-side one.
PKG_CLAIMED_MAP = MAP + (
    "| [x] | A package thing | `Services/PkgOrphan.swift` | `WiredTests.swift` |\n"
)
code, out, err = run(fixture(pkg_services={"PkgOrphan.swift": PKG_ORPHAN}, features=PKG_CLAIMED_MAP))
check_equal("an [x] row citing an unreached package service fails", 1, code)
check_contains(
    "and is reported the same way an app-side one is",
    "docs/features.md:6: Services/PkgOrphan.swift (declares PkgOrphanKind, PkgOrphanService)",
    err,
)

# And a package service the row cites that *is* reached counts toward the
# judged total in the OK line, the same as an app-side one.
PKG_WIRED_MAP = MAP + (
    "| [x] | A package thing | `Services/PkgWired.swift` | `WiredTests.swift` |\n"
)
code, out, err = run(fixture(features=PKG_WIRED_MAP))
check_equal("an [x] row citing a reached package service passes", 0, code)
check_contains(
    "and is counted among the judged rows",
    "each of the 2 services an [x] row cites is named by another source under a swept root",
    out,
)

# A `Services/*.swift` citation that resolves only under a test root -- not
# under any of "service_roots" -- is not a candidate for this pass at all:
# there is no app or package source to read for names. It still resolves, so
# the first pass leaves it alone, and the judged count in the OK line does not
# grow for a row this pass never actually read.
TEST_ONLY_MAP = MAP + (
    "| [x] | Only a test source | `Services/TestOnly.swift` | `WiredTests.swift` |\n"
)
test_only = fixture(features=TEST_ONLY_MAP)
write(
    os.path.join(test_only, "Kiteloop", "KiteloopTests", "Services", "TestOnly.swift"),
    "struct TestOnly {}\n",
)
code, out, err = run(test_only)
check_equal(
    "an [x] row citing a Services/*.swift path only a test root has still passes",
    0,
    code,
)
check_contains(
    "and the judged count does not include it -- there is no service source under a swept root",
    "each of the 1 services an [x] row cites is named by another source under a swept root",
    out,
)

# --- Against this checkout ----------------------------------------------------
#
# The fixtures say what the passes do; this says what they find here. The run is
# the preflight's: from the repo root, no `--config`, so it is the quality.json
# at the root that is read. The counts are read off the tree rather than written
# down, so the case keeps meaning the same thing after a service is wired up,
# added or deleted — and it fails if the line ever prints a number that came from
# somewhere other than what it judged.

checkout_config = os.path.join(REPO, "quality.json")
if os.path.isfile(checkout_config) and "features_map" in json.load(open(checkout_config)):
    proc = subprocess.run(
        [sys.executable, SCRIPT], cwd=REPO, capture_output=True, text=True,
    )
    code, out, err = proc.returncode, proc.stdout, proc.stderr
    check_equal("the check passes against this tree", 0, code)
else:
    check("this checkout configures no features map, so it is not judged", True)

if os.path.isfile(checkout_config) and "features_map" in json.load(open(checkout_config)):
    real = module.Settings(quality_config.Config(CONFIG))
    real_sources = module.swept_sources(real)
    tree_services = module.service_files(real_sources)
    tree_service_paths = [module.repo_path(service) for service in tree_services]
    expected_exempt = len([p for p in tree_service_paths if p in real.exempt_services])
    per_root = module.join_and(
        [
            f"{len([s for s in tree_services if s.label == label and module.repo_path(s) not in real.exempt_services])} "
            f"services under {label}/Services/"
            for label in real.service_roots
        ]
    )
    check_contains(
        "the OK line's counts are the services this tree has, under each swept root",
        f"so is each of {per_root} ({expected_exempt} exempt)",
        out,
    )
    # The list is empty here, and empty is the state the ratchet is trying to reach —
    # so what is worth freezing is not that exemptions exist but that none has rotted.
    # Nine did: the services they named were retired out of the tree and the entries
    # stayed behind, naming nothing. That is the same dead reference the first pass
    # exists to catch, one level up — and it is what made this case fail, rather than
    # anything it was guarding.
    stale_exemptions = sorted(set(real.exempt_services) - set(tree_service_paths))
    check(
        "every exemption names a service this tree actually has",
        not stale_exemptions,
        "exemptions naming nothing in the tree: " + ", ".join(stale_exemptions),
    )

    # And the counts are not vacuous. A pass that judged nothing would satisfy the
    # count assertion above while checking no service at all, which is the "green
    # because it never ran" reading in miniature.
    check(
        "the check judged services rather than an empty tree",
        len(tree_service_paths) - expected_exempt > 0,
        "no service under a swept root's Services/ was judged",
    )

    # The sweep reaches the package, not just the app target: services under
    # each root's Services/ are judged, and the package alone contributes some.
    check(
        "the pass swept the package root as well as the app target",
        any(s.label != APP_ROOT for s in tree_services),
        "no service under the package root was found",
    )

    # The pass reads services and only services: the count above is each swept
    # root's Services/ directory, not its Swift files, whose number is far larger.
    check(
        "the pass counts services rather than every swept source",
        len(tree_services) < len(real_sources),
        "every swept source resolved as a service",
    )

    for path in fixtures:
        shutil.rmtree(path, ignore_errors=True)

    print("\n%s" % ("FAILED" if failed else "All checks passed."))
    sys.exit(1 if failed else 0)

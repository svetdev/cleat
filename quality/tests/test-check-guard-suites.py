#!/usr/bin/env python3
"""test-check-guard-suites — assert quality/bin/check-guard-suites.py.

Nine guard suites sat outside the test runner's `PREFLIGHT` array when this
was written: two under `quality/tests/` and seven under a tooling directory.
Each asserts something real and none runs anywhere a
standing measurement reads them — the third time this exact class of bug has
shipped. The check this asserts reads the `test-*.py`/`test-*.sh` files under
the roots `"guard_suites"."roots"` names and the `PREFLIGHT` array out of the
script `"guard_suites"."preflight"` names, and fails the suites named by
neither the array nor `"exempt"`. `"exempt"` is a ratchet the same shape as
`"exempt"` in `check-reachability.py`: a suite already outside the
preflight is named there with the item that tracks wiring it in, and a stale
entry -- naming a path no swept root has -- is its own failure rather than a
silent no-op, since left alone it would go on exempting whatever suite is
later added at that path.

A `test-*.py`/`test-*.sh` file under a swept root need not be a suite at all
-- `scripts/tools/test-move.py` is a tool that moves a test between
targets, not a test, and has a guard suite of its own. A path
in `"not_suites"`, keyed the same way as `"exempt"`, is dropped from the
sweep before `"exempt"` is even consulted, so it is counted in neither the
checked figure nor the exempt one, and a path the key names that no swept
root has fails the same way a stale `"exempt"` entry does. The key is
optional -- a `quality.json` without it judges exactly as one with an empty
map.

Every case runs the real script over a throwaway checkout: a `quality.json`
naming a `run-unit-suite.sh` stand-in and the swept roots, a handful of
`test-*` files under them, and a `PREFLIGHT` array listing some. The script is
handed that tree with `--config`, so it judges the fixture and nothing here
can be answered by the state of this checkout -- except the last case, which
runs the script against this repository's own `quality.json` the way the
preflight does.

It starts no build, reads no process list and writes nothing outside a
temporary directory, so it is safe to run -- on its own or as part of the
suite -- while the project's own app is running.

  python3 quality/tests/test-check-guard-suites.py
"""

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
SCRIPT = os.path.join(BIN, "check-guard-suites.py")
CONFIG = os.path.join(REPO, "quality.json")

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


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)


# --- The fixture ----------------------------------------------------------
#
# Three swept roots, mirroring this repository's own: `quality/tests`,
# `scripts` and `scripts/tools`. One suite listed in PREFLIGHT under
# each, plus one exempt suite -- that is the green shape every case starts
# from, so a case says what it changes and nothing else.
ROOTS = ["quality/tests", "scripts", "scripts/tools"]
PREFLIGHT_SCRIPT = "scripts/run-unit-suite.sh"
EXEMPT_SUITE = "quality/tests/test-dormant.py"
EXEMPT = {EXEMPT_SUITE: "a fixture-only exemption"}

DEFAULT_ENTRIES = [
    "quality/tests/test-a.py",
    "scripts/test-b.py --quiet",
    "scripts/tools/test-c.py",
]


def preflight_script(entries):
    """A run-unit-suite.sh stand-in carrying only the PREFLIGHT array -- the
    check reads it as text, so nothing else it does needs to exist."""
    body = "\n".join('  "%s"' % entry for entry in entries)
    return "#!/bin/bash\nPREFLIGHT=(\n%s\n)\n" % body


def fixture(entries=None, extra_suites=None, exempt=None, preflight=None, not_suites=None, patterns=None):
    """A throwaway checkout, with the quality.json the script is handed.

    `not_suites` is left out of the written config entirely when not given,
    so the default fixture exercises a `quality.json` with no such key --
    the shape most cases run under.
    """
    root = tempfile.mkdtemp(prefix="test-check-guard-suites-")
    fixtures.append(root)

    guard_suites = {
        "preflight": PREFLIGHT_SCRIPT,
        "roots": ROOTS,
        "exempt": EXEMPT if exempt is None else exempt,
    }
    if not_suites is not None:
        guard_suites["not_suites"] = not_suites
    if patterns is not None:
        guard_suites["patterns"] = patterns
    write(os.path.join(root, "quality.json"), json.dumps({"guard_suites": guard_suites}, indent=2))

    write(
        os.path.join(root, PREFLIGHT_SCRIPT),
        preflight if preflight is not None else preflight_script(
            DEFAULT_ENTRIES if entries is None else entries
        ),
    )

    for entry in ["quality/tests/test-a.py", "scripts/test-b.py", "scripts/tools/test-c.py"]:
        write(os.path.join(root, entry), "# suite\n")
    write(os.path.join(root, EXEMPT_SUITE), "# dormant\n")

    for path in (extra_suites or []):
        write(os.path.join(root, path), "# extra suite\n")

    return root


def run(root, *args):
    """Run the script under the quality.json in `root`."""
    proc = subprocess.run(
        [sys.executable, SCRIPT, "--config", os.path.join(root, "quality.json"), *args],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


print("test-check-guard-suites")

# --- What a suite is named like ---------------------------------------------
#
# `"patterns"` says what filenames are suites -- a pytest tree is `test_*.py`.
# With it set, the default `test-*` shapes are not swept at all, and a file
# matching the given shape is judged like any other suite.

code, out, err = run(fixture(patterns=["test_*.py"], extra_suites=["scripts/test_orphan.py"],
                             entries=["scripts/test_listed.py"]), )
check_equal("under a custom pattern an unlisted matching file fails", 1, code)
check_contains("the unlisted file is named", "scripts/test_orphan.py", err)
check_equal("the default test-* files are not swept under a custom pattern",
            0, err.count("test-a.py") + err.count("test-b.py") + err.count("test-c.py"))

# --- The green shape --------------------------------------------------------

code, out, err = run(fixture())
check_equal("a tree whose suites are all listed or exempt passes", 0, code)
check_contains(
    "the OK line names the roots, the count checked, the count exempt and the "
    "count not a suite",
    "OK: all 3 guard suite(s) under quality/tests/, scripts/, scripts/tools/ "
    "are named in scripts/run-unit-suite.sh's PREFLIGHT array (1 exempt, 0 not a suite)",
    out,
)
check_equal("a passing run says nothing on stderr", "", err)

code, out, err = run(fixture(), "--quiet")
check_equal("--quiet prints nothing when everything passes", ("", "", 0), (out, err, code))

# --- A suite named by no PREFLIGHT entry ------------------------------------
#
# The failure this check exists for: a test-* file under a swept root that no
# PREFLIGHT entry runs.

code, out, err = run(fixture(extra_suites=["scripts/test-orphan.py"]))
check_equal("a guard suite no PREFLIGHT entry names fails the check", 1, code)
check_contains("the failure names the offending suite's path", "scripts/test-orphan.py", err)
check_contains(
    "the failure says what an unlisted suite means",
    "run nowhere",
    err,
)
check_contains(
    "the failure says where an exemption goes, for a suite that must stay unwired",
    'add it to "exempt" in quality.json',
    err,
)
check_equal("the failure is on stderr, and stdout carries none of it", "", out)

code, out, err = run(fixture(extra_suites=["scripts/test-orphan.py"]), "--quiet")
check_equal("--quiet still fails", 1, code)
check_contains("--quiet still names the offender", "scripts/test-orphan.py", err)

# A suite under scripts/tools/, exercising the third root the same way.
code, out, err = run(fixture(extra_suites=["scripts/tools/test-orphan.py"]))
check_equal("an orphan under scripts/tools/ fails the same way", 1, code)
check_contains(
    "and is named by its own path",
    "scripts/tools/test-orphan.py",
    err,
)

# --- A suite one directory deeper than a swept root's top level -------------
#
# The bug this item fixes: a suite sitting beneath a swept root, but not
# directly in it, used to be invisible to the sweep. It must now be seen, and
# reported by its path relative to the repository root -- not root-label
# joined to basename, which is meaningless once a suite is nested.

code, out, err = run(fixture(extra_suites=["quality/tests/nested/test-deep.py"]))
check_equal("a suite nested below a swept root's top level fails the check", 1, code)
check_contains(
    "and is named by its path relative to the repo root",
    "quality/tests/nested/test-deep.py",
    err,
)

# --- Overlapping roots: a nested suite is seen once, not once per root ------
#
# `scripts` and `scripts/tools` are both swept roots here, the way
# quality.json spells them today, so a suite under scripts/tools/ sits
# beneath both. It must be counted, and reported, exactly once.

code, out, err = run(fixture(extra_suites=["scripts/tools/nested/test-deep.py"]))
check_equal("an unlisted suite under overlapping roots fails once, not twice", 1, code)
check_contains(
    "the failure reports exactly one guard suite",
    "FAIL: 1 guard suite(s) run nowhere",
    err,
)
check_equal(
    "and names it exactly once, not once per overlapping root",
    1,
    err.count("scripts/tools/nested/test-deep.py"),
)

code, out, err = run(fixture(
    extra_suites=["scripts/tools/nested/test-deep.py"],
    entries=DEFAULT_ENTRIES + ["scripts/tools/nested/test-deep.py"],
))
check_equal("the same nested suite, once covered by PREFLIGHT, also passes", 0, code)
check_contains(
    "and the OK count includes it once, not once per overlapping root",
    "OK: all 4 guard suite(s)",
    out,
)

# --- The exemption list ------------------------------------------------------
#
# The green shape above already carries an exempt suite; this says the check
# would have caught it, which is the only thing that makes the exemption mean
# anything. Same tree, same content, the one entry taken out of the list.

code, out, err = run(fixture(exempt={}))
check_equal("the same suite fails once it is off the exemption list", 1, code)
check_contains(
    "which is what the exemption was suppressing",
    EXEMPT_SUITE,
    err,
)

# An "exempt" entry naming no file under any swept root at all -- the tree
# moved past it rather than someone taking it off the list. Left unreported,
# it would go on silently exempting whatever suite a later commit adds there.
code, out, err = run(fixture(exempt={
    **EXEMPT,
    "scripts/test-ghost.py": "a stale reason nobody deleted",
}))
check_equal(
    "an exempt entry naming a suite the tree does not have fails",
    1,
    code,
)
check_contains("the failure names the entry's path", "scripts/test-ghost.py", err)
check_contains(
    "the failure prints the reason recorded beside the entry",
    "a stale reason nobody deleted",
    err,
)
check_contains(
    "the failure names the file the entry lives in",
    '"exempt" entry(ies) in quality.json',
    err,
)
check_contains("the failure says to delete the entry rather than add a file", "Delete the entry", err)
check_absent(
    "a still-valid exemption is not reported alongside the stale one",
    "%s: " % EXEMPT_SUITE,
    err,
)

# --- A retired exemption: PREFLIGHT now runs an "exempt" suite --------------
#
# The check only ever asks whether `checked` -- the suites not on the exempt
# list -- are named by PREFLIGHT, so an exempt suite wired in since is never
# looked at again unless this is asked directly. Same tree as the green
# shape, but the exempt suite's path is now also a PREFLIGHT entry.

code, out, err = run(fixture(entries=DEFAULT_ENTRIES + [EXEMPT_SUITE]))
check_equal("an exempt suite PREFLIGHT now runs fails the check", 1, code)
check_contains("the failure names the retired entry's path", EXEMPT_SUITE, err)
check_contains(
    "the failure prints the reason recorded beside the entry",
    EXEMPT[EXEMPT_SUITE],
    err,
)
check_contains(
    "the failure names the file the entry lives in",
    '"exempt" entry(ies) in quality.json',
    err,
)
check_contains(
    "the failure says the suite is now run, not still unwired",
    "PREFLIGHT array now runs",
    err,
)
check_contains("the failure says to delete the entry rather than leave it", "Delete the entry", err)

# A tree with an unlisted suite, a stale "exempt" entry and a retired
# "exempt" entry all at once -- every FAIL block must appear, separated the
# same way the existing two already are.
code, out, err = run(fixture(
    entries=DEFAULT_ENTRIES + [EXEMPT_SUITE],
    extra_suites=["scripts/test-orphan.py"],
    exempt={**EXEMPT, "scripts/test-ghost.py": "a stale reason nobody deleted"},
))
check_equal("unlisted, stale and retired all firing at once fails", 1, code)
check_contains("the unlisted block names its offender", "scripts/test-orphan.py", err)
check_contains("the stale block names its offender", "scripts/test-ghost.py", err)
check_contains("the retired block names its offender", EXEMPT_SUITE, err)
check_equal(
    "all three blocks print, one FAIL header each",
    3,
    err.count("FAIL:"),
)

# --- "not_suites": a swept file that is not a guard suite at all ------------
#
# scripts/test-orphan.py would otherwise fail as unlisted (it is named by no
# PREFLIGHT entry and is not exempt). Naming it in "not_suites" instead drops
# it from the sweep before "exempt" is even consulted -- it must pass, and be
# counted on its own rather than folded into the exempt figure.

NOT_A_SUITE = "scripts/test-orphan.py"

code, out, err = run(fixture(
    extra_suites=[NOT_A_SUITE],
    not_suites={NOT_A_SUITE: "it is a tool, not a test"},
))
check_equal("a suite named in not_suites is not reported as unlisted", 0, code)
check_contains(
    "and the OK line counts it as not a suite, not as exempt",
    "OK: all 3 guard suite(s) under quality/tests/, scripts/, scripts/tools/ "
    "are named in scripts/run-unit-suite.sh's PREFLIGHT array (1 exempt, 1 not a suite)",
    out,
)

# A path in "not_suites" that is also listed in "exempt" is still dropped
# before "exempt" is consulted -- it counts once, as not a suite, not twice.
code, out, err = run(fixture(
    extra_suites=[NOT_A_SUITE],
    exempt={**EXEMPT, NOT_A_SUITE: "would have been exempt too"},
    not_suites={NOT_A_SUITE: "it is a tool, not a test"},
))
check_equal("a not_suites entry wins over an exempt entry for the same path", 0, code)
check_contains(
    "and it is counted once, as not a suite",
    "(1 exempt, 1 not a suite)",
    out,
)

# A "not_suites" entry naming no file under any swept root -- the same shape
# as a stale "exempt" entry, and reported the same way.
code, out, err = run(fixture(not_suites={
    "scripts/test-ghost.py": "a stale reason nobody deleted",
}))
check_equal("a not_suites entry naming a path no swept root has fails", 1, code)
check_contains("the failure names the entry's path", "scripts/test-ghost.py", err)
check_contains(
    "the failure prints the reason recorded beside the entry",
    "a stale reason nobody deleted",
    err,
)
check_contains(
    "the failure names the file the entry lives in, and the key it is stale in",
    '"not_suites" entry(ies) in quality.json',
    err,
)
check_contains("the failure says to delete the entry rather than add a file", "Delete the entry", err)

# A quality.json with no "not_suites" key at all -- every fixture() call
# above this section already ran under exactly that config, so this is only
# the direct assertion: the verdict and the OK line's count are unaffected.
code, out, err = run(fixture())
check_equal("a config without a not_suites key still passes", 0, code)
check_contains(
    "and reports zero not-suites, not an error",
    "(1 exempt, 0 not a suite)",
    out,
)

# --- --list: the swept suites, not_suites dropped, exempt kept --------------
#
# What quality/tests/run.sh drives itself from. It must print exactly the
# suites a run would run -- exempt suites included, since running them is
# the point of a list a runner drives itself from -- and nothing else, so a
# runner piping this straight into a loop sees only paths.

code, out, err = run(fixture(), "--list")
check_equal("--list exits 0 over the green fixture", 0, code)
check_equal(
    "and prints exactly the swept suites, root by root, exempt kept",
    "quality/tests/test-a.py\n"
    "quality/tests/test-dormant.py\n"
    "scripts/test-b.py\n"
    "scripts/tools/test-c.py\n",
    out,
)
check_equal("--list prints nothing on stderr", "", err)

# not_suites is dropped from --list the same way it is dropped from the
# checked and exempt figures -- it is not a suite, so it is not run.
code, out, err = run(fixture(
    extra_suites=[NOT_A_SUITE],
    not_suites={NOT_A_SUITE: "it is a tool, not a test"},
), "--list")
check_equal("--list drops a not_suites entry", 0, code)
check_absent("the dropped path is not printed", NOT_A_SUITE, out)

# --list does not care whether PREFLIGHT covers every suite -- it is the
# input to a runner, not a verdict, so an orphan suite still prints and
# still exits 0.
code, out, err = run(fixture(extra_suites=["scripts/test-orphan.py"]), "--list")
check_equal("--list exits 0 even with a suite PREFLIGHT names nowhere", 0, code)
check_contains("and still prints the orphan suite", "scripts/test-orphan.py\n", out)

# --- Reading the PREFLIGHT array ---------------------------------------------
#
# An entry carries flags after its path (`--quiet`, and the like); only the
# first word is the path a suite is judged against.

code, out, err = run(fixture(entries=["scripts/test-b.py --quiet --extra-flag"]),)
check_equal(
    "a PREFLIGHT entry with flags is matched by its leading path alone",
    1,
    code,
)
check_contains(
    "and the two suites the flagged entry does not cover are the ones reported",
    "quality/tests/test-a.py",
    err,
)
check_contains("both missing suites are named", "scripts/tools/test-c.py", err)

code, out, err = run(fixture())
check_absent(
    "a suite whose entry carries no flags is not reported",
    "scripts/test-b.py\n",
    err,
)

# A file under a swept root not shaped like `test-*.py`/`test-*.sh` is not a
# guard suite and is not judged at all -- the sweep is scoped by the SUITE_RE
# name, not by "everything in the directory".
code, out, err = run(fixture(extra_suites=["scripts/helpers.py"]))
check_equal("a non test-* file under a swept root is not swept", 0, code)
check_contains(
    "the checked count does not grow for it",
    "OK: all 3 guard suite(s)",
    out,
)

# --- No PREFLIGHT array to read ----------------------------------------------
#
# A run-unit-suite.sh stand-in with no PREFLIGHT array at all cannot be read
# as evidence that anything is wired up -- it is an error, not a pass by
# default, and the same is true of an empty one.

code, out, err = run(fixture(preflight="#!/bin/bash\necho nothing here\n"))
check_equal("a script with no PREFLIGHT array exits 2", 2, code)
check_contains("naming what could not be read", "no PREFLIGHT array found", err)

code, out, err = run(fixture(preflight="#!/bin/bash\nPREFLIGHT=(\n)\n"))
check_equal("an empty PREFLIGHT array exits 2", 2, code)
check_contains("naming that it is empty", "PREFLIGHT array is empty", err)

# --- Which quality.json is read, and what it must carry ----------------------

sectionless = fixture()
write(os.path.join(sectionless, "quality.json"), json.dumps({"project": "Fixture"}))
code, out, err = run(sectionless)
check_equal("a quality.json without a guard_suites section exits 2", 2, code)
check_contains("and names the section it lacks", '"guard_suites"', err)

keyless = fixture()
write(os.path.join(keyless, "quality.json"), json.dumps({
    "guard_suites": {"preflight": PREFLIGHT_SCRIPT, "roots": ROOTS}
}))
code, out, err = run(keyless)
check_equal("a guard_suites section missing exempt exits 2", 2, code)
check_contains("and names the missing key", '"exempt"', err)

missing_script = fixture()
os.remove(os.path.join(missing_script, PREFLIGHT_SCRIPT))
code, out, err = run(missing_script)
check_equal("a missing preflight script exits 2", 2, code)
check_contains("naming the script it could not read", PREFLIGHT_SCRIPT, err)

# --- Against this checkout ---------------------------------------------------
#
# The fixtures say what the check does; this says what it finds here. The run
# is the preflight's: from the repo root, no `--config`, so it is the
# quality.json at the root that is read. The counts are read off the tree
# rather than written down, so this keeps meaning the same thing after a
# suite is wired up, added or deleted.

if os.path.isfile(CONFIG) and "guard_suites" in json.load(open(CONFIG)):
    proc = subprocess.run(
        [sys.executable, SCRIPT, "--quiet"], cwd=REPO, capture_output=True, text=True,
    )
    check_equal("--quiet passes against this tree", 0, proc.returncode)
    check_equal("and --quiet prints nothing on a pass", "", proc.stdout)

    proc = subprocess.run(
        [sys.executable, SCRIPT], cwd=REPO, capture_output=True, text=True,
    )
    check_equal("a bare run against this tree also passes", 0, proc.returncode)
    check_contains(
        "and the OK line says how many suites this tree has, how many are "
        "exempt and how many are not suites",
        "not a suite)",
        proc.stdout,
    )
    check(
        "no guard suite here is exempt any more — the standing check runs them all",
        "(0 exempt, 1 not a suite)" in proc.stdout,
        proc.stdout,
    )
else:
    check("this checkout configures no guard suites, so it is not judged", True)

for path in fixtures:
    shutil.rmtree(path, ignore_errors=True)

print("\n%s" % ("FAILED" if failed else "All checks passed."))
sys.exit(1 if failed else 0)

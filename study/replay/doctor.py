#!/usr/bin/env python3
"""doctor — whether a language for the replay works on a repository, before any agent runs.

  doctor.py <rust|ts|replay.json> <clone> [sha]      (sha: default the clone's HEAD)

On a copy of the clone's tree at the commit: the dependencies installed, the production and
test files counted by the language's rules, the whole suite run and read, and the check where
there is one. Prints what it found and, for anything that would stop a replay, what to change
in the adapter. Exits 1 when the replay could not run here: the install fails, no production
file is found, or the suite reports no test.
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lang as languages   # noqa: E402
import draw                # noqa: E402  (lang put the paired runs on the path)
import measure             # noqa: E402

SAMPLE = 5


def files(lang, root):
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root,
                            capture_output=True, text=True).stdout.split("\n")
    return [p for p in listed if p and lang.production(p)], [p for p in listed if p and lang.is_test(p)]


def report(lang, root):
    """(lines to print, whether a replay can run)."""
    out = []
    recipe, why = lang.install(root)
    out.append("install: " + why)
    if recipe is None:
        return out + ["  fix the adapter's install command; its output:"] + lang.output(), False
    prods, tests = files(lang, root)
    out.append("production files: %d (%s lines); test files: %d"
               % (len(prods), measure.measure(lang.name, root)["lines"], len(tests)))
    if not prods:
        return out + ["  no production file: set the adapter's languages, or its production globs"], False
    got = lang.suite(root)
    passed = sum(1 for ok in got["tests"].values() if ok)
    out.append("suite: %d tests read, %d pass, %d fail" % (len(got["tests"]), passed, len(got["tests"]) - passed))
    out += ["  e.g. " + t for t in sorted(got["tests"])[:SAMPLE]]
    if not got["tests"]:
        return out + ["  no test read: the test command must run {files} and write JUnit XML to {out}; its output:"] + lang.output(), False
    if got["silent"]:
        out.append("  %d test files reported no test, e.g. %s (a test id that does not start with its file "
                   "makes every change look like it stopped tests running)" % (len(got["silent"]), got["silent"][0]))
    if passed < len(got["tests"]):
        out.append("  failing at the start: a chain accepts a change against the tree it was handed, so these do "
                   "not stop a replay, but a commit where the suite passes is a cleaner start")
    out.append("check: %d errors" % len(got["errors"]))
    return out, True


def main(lang_name, clone, sha="HEAD"):
    lang = languages.get(lang_name)
    with tempfile.TemporaryDirectory(prefix="replay-doctor-") as tmp:
        root = os.path.join(tmp, "repo")
        draw.tree(os.path.abspath(clone), sha, root)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        lines, ok = report(lang, root)
    print("\n".join(lines))
    print("ready for a replay" if ok else "not ready")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        raise SystemExit(__doc__)
    sys.exit(main(*sys.argv[1:]))

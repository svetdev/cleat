#!/usr/bin/env python3
"""check-test-hygiene — fail if a count the test suite has been cleaned of rises again.

Each count is a habit the suite was migrated away from, one file at a time: a
fixed `Task.sleep` where a wait for the thing belongs (`eventually`), a
loopback-port probe or an `isListening` copied into a file instead of the
shared harness, a `URLSession` built by hand for loopback, a temp directory
built by hand instead of `makeTemporaryDirectory`, a file-local
`waitUntil`/`settle`. Each was copied dozens of times before the one spelling
existed, because copying the neighbour is what a new test file does, and
nothing said otherwise. This says otherwise: the ceiling for each is the count
on the day it was written, and a count above its ceiling fails the preflight.
A count that falls is the new ceiling to write down — the success line prints
them, so lowering one is a one-line commit.

The habits, their ceilings and the test trees live in the `hygiene` section of
`quality.json`: `roots` (the trees to count in, relative to the file),
`skip_dirs` (directory names not counted — the shared helpers live there),
`extensions` (the file suffixes counted; `.swift` when absent),
`test_file_roots` (trees that mix production and test code, counted through
their test files only: root → the test suffixes), and
`habits`, name → a regex `pattern` over code, the `ceiling`, and `use`, the one
spelling to write instead. A ceiling is raised only on purpose, with the reason
in the commit, and a raised ceiling should read as a ledger: each increment
names the sites it admits and why each has no better spelling — a debounce that
has to be waited out and nothing says when it has; a listener-readiness retry;
a window asserting a stray byte does *not* end a stream; a socket-write poll
from a background thread where an async `eventually` cannot be called. The
number is then defensible line by line rather than a total that drifted.

Sites are counted in code only — a mention in a comment is not a habit. A
failure lists, under each over-ceiling habit, the repo-relative `path:line` of
every site it matched, up to 20 with a `… and N more` tail — the fix starts
where the check stopped. Reads the tree and starts no build: safe while the
app is running.

  quality/bin/check-test-hygiene.py
  quality/bin/check-test-hygiene.py --quiet
  quality/bin/check-test-hygiene.py --config PATH                # a quality.json other than the nearest
  quality/bin/check-test-hygiene.py --tests DIR [--tests DIR]   # judge other trees (the tests use this)
"""

import argparse
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
from extractors import patterns


def strip_code(text):
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


DEFAULT_EXTENSIONS = [".swift"]


def source_files(roots, skip_dirs, extensions=DEFAULT_EXTENSIONS):
    """Every file under `roots` with one of `extensions` — `.swift` unless the config's
    `hygiene.extensions` says otherwise, so a Rust, TypeScript or Kotlin suite counts too."""
    return patterns.files(roots, extensions, skip_dirs)



def test_files_in(mixed_roots, skip_dirs):
    """Files under trees that mix production and test code, kept by test suffix —
    `hygiene.test_file_roots`: {"apps/web/src": [".test.ts", ".test.tsx"]}."""
    for root, suffixes in mixed_roots.items():
        for path in source_files([root], skip_dirs, tuple(suffixes)):
            yield path


def count(roots, skip_dirs, habits, repo_root, extensions=DEFAULT_EXTENSIONS, mixed_roots=None):
    """Per habit, the total and every site (repo-relative `path:line`) it matched at."""
    totals = {name: 0 for name in habits}
    sites = {name: [] for name in habits}
    files = list(source_files(roots, skip_dirs, extensions)) + list(test_files_in(mixed_roots or {}, skip_dirs))
    regexes = {name: habit["pattern"] for name, habit in habits.items()}
    for rel, line, _text, name in patterns.sites(files, regexes, repo_root, prepare=strip_code):
        totals[name] += 1
        sites[name].append("%s:%d" % (rel, line))
    return totals, sites


def resolve_roots(args, config):
    """The test roots to judge: `--tests` overrides, else `hygiene.roots` resolved against
    the repo root."""
    if args.tests:
        return [os.path.abspath(r) for r in args.tests]
    return [os.path.join(config.root, os.path.expanduser(r)) for r in config.get("hygiene", "roots")]


def report_failure(over, sites, config):
    print("FAIL: %d test habit(s) the suite was cleaned of have grown back:" % len(over))
    for name, got, ceiling, instead in over:
        print("  %s: %d, ceiling %d — use %s" % (name, got, ceiling, instead))
        habit_sites = sites[name]
        for site in habit_sites[:20]:
            print("    %s" % site)
        if len(habit_sites) > 20:
            print("    … and %d more" % (len(habit_sites) - 20))
    print("Replace the new sites with the one spelling, or — on purpose — raise the ceiling under \"hygiene\" in %s and say why in the commit." % config.file)


def main():
    parser = argparse.ArgumentParser(description="fail if a migrated-away test habit grows back")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--tests", action="append", help="a test tree to judge instead of the config's")
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    config = quality_config.load(args.config)
    habits = config.get("hygiene", "habits")
    skip_dirs = set(config.get("hygiene", "skip_dirs"))
    roots = resolve_roots(args, config)
    extensions = config.section("hygiene").get("extensions", DEFAULT_EXTENSIONS)
    mixed = {os.path.join(config.root, os.path.expanduser(r)): suffixes
             for r, suffixes in config.section("hygiene").get("test_file_roots", {}).items()}
    totals, sites = count(roots, skip_dirs, habits, config.root, extensions, mixed)
    over = [(n, totals[n], h["ceiling"], h["use"]) for n, h in habits.items() if totals[n] > h["ceiling"]]
    if over:
        report_failure(over, sites, config)
        return 1
    if not args.quiet:
        print("OK: test hygiene holds — " + ", ".join("%s %d/%d" % (n, totals[n], h["ceiling"]) for n, h in habits.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

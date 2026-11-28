#!/usr/bin/env python3
"""check-escapes — fail on a new place where the code opts out of a check.

An escape is what an agent reaches for to make an error or a red test go away
without addressing it: a type check switched off (`any`, `as!`, `unwrap()`,
`!!`, `# type: ignore`, `@ts-ignore`), a lint rule silenced (`eslint-disable`,
`# noqa`, `#[allow(...)]`, `swiftlint:disable`), a test skipped (`.skip`,
`.only`, `@Disabled`, `XCTSkip`, `#[ignore]`), an error swallowed (`|| true`).
Each has a legitimate use, and each is also the cheapest way to turn red
green. So they are ratcheted **by site**, not counted: the baseline records
every line that carries one on adoption day, and a line not in it fails. A
count of 41 would let an old escape vanish as a new one appears, forever; a
site cannot.

A test is not production code: `unwrap()` on a fixture, `as any` on a
deliberately malformed input and a non-null assertion on a value the test just
built are how tests are written, and a gate that refuses them refuses most new
tests. In a file matching a `tests` glob the one escape that counts is the
skipped test, the one that turns a red suite green.

The site's identity is its file and the text of its line, so editing the file
above it does not unbaseline it; a line whose only change is a renamed
identifier — the same escape, the same shape — is the same site too, so a
rename is not new debt. The same line twice in one file is one entry with a
count, and that count ratchets too.

Everything that names the project is the `escapes` section of quality.json:

  "escapes": {
    "roots":     ["src", "tests"],                  # trees read (default: the config's directory)
    "languages": ["python", "typescript"],          # which built-in pattern sets apply
    "skip_dirs": ["node_modules", "vendor"],        # directory names pruned (defaults below apply too)
    "exclude":   ["*.test.ts"],                     # file globs left unread (a test tree that shares a root)
    "tests":     ["*/tests/*", "*.test.*"],         # test files: only a skipped test is a site there
    "skip_rust_tests": true,                        # sites inside `#[cfg(test)]` modules are not production (default)
    "patterns":  {"todo bang": "TODO!"},            # extra patterns of the project's own, name → regex
    "baseline":  "quality/escapes-baseline.json"    # the ratchet
  }

  quality/bin/check-escapes.py
  quality/bin/check-escapes.py --quiet
  quality/bin/check-escapes.py --write-baseline     # accept every site that exists today
  quality/bin/check-escapes.py --strict             # CI: a loose baseline fails too
  quality/bin/check-escapes.py --list-languages     # the built-in pattern sets
"""

import argparse
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
import ratchet
from extractors import census, languages, patterns

SECTION = "escapes"

LANGUAGES = languages.LANGUAGES   # the table lives in extractors/languages.py; every gate with a `languages` list reads it

DEFAULT_SKIP_DIRS = patterns.DEFAULT_SKIP_DIRS   # the walker prunes these; the name stays for callers


language = languages.language


TEST_ESCAPE = "skipped test"   # the one built-in kind that counts in a test file


def _test_code(repo_root, skip_rust_tests, tests):
    """is(rel, line, kind): whether a site is test code the gate does not judge — inside an
    inline Rust test module when `skip_rust_tests` is on, or in a file matching a `tests`
    glob unless it skips a test."""
    ranges = {}

    def inline(rel, line):
        if rel not in ranges:
            ranges[rel] = patterns.rust_test_ranges(os.path.join(repo_root, rel)) if skip_rust_tests else []
        return patterns.in_ranges(ranges[rel], line)

    def named(rel, kind):
        return kind != TEST_ESCAPE and bool(tests) and patterns.excluded(os.path.join(repo_root, rel), tests)

    return lambda rel, line, kind: named(rel, kind) or inline(rel, line)


def _collect(seen, paths, regexes, repo_root, in_tests, skipped):
    """Record every site; one in test code is counted in `skipped` instead."""
    for site in patterns.sites(paths, regexes, repo_root):
        if in_tests(site[0], site[1], site[3]):
            skipped[0] += 1
        else:
            _record(seen, *site)


def findings(section, config):
    """Every escape site under the configured roots, one Finding per (file, line text),
    with how many times that line carries it."""
    roots = config.paths(section.get("roots", ["."]))
    skip = set(section.get("skip_dirs", []))
    exclude = section.get("exclude", [])
    in_tests = _test_code(config.root, section.get("skip_rust_tests", True), section.get("tests", []))
    languages = [language(name) for name in section.get("languages", [])]
    seen, skipped = {}, [0]
    for spec in languages:
        _collect(seen, patterns.files(roots, spec["suffixes"], skip, exclude), spec["patterns"], config.root, in_tests, skipped)
    if section.get("patterns"):
        suffixes = sorted({s for spec in languages for s in spec["suffixes"]}) or [""]
        _collect(seen, patterns.files(roots, suffixes, skip, exclude), section["patterns"], config.root, in_tests, skipped)
    out = [ratchet.Finding(rel, line, text, {"escape": kind, "count": count})
           for (rel, text), (line, kind, count) in seen.items()]
    out.sort(key=lambda f: (f.file, f.line))
    return out, skipped[0]


def _record(seen, rel, line, text, kind):
    key = (rel, text)
    if key in seen:
        first_line, first_kind, count = seen[key]
        seen[key] = (first_line, first_kind, count + 1)
    else:
        seen[key] = (line, kind, 1)


def same_site(finding, entry):
    """One escape under another spelling: the same kind, on a line of the same shape."""
    return entry.get("escape") == finding.values.get("escape") and ratchet.shape(entry["text"]) == ratchet.shape(finding.text)


def list_languages():
    for name in sorted(LANGUAGES):
        print("%-11s %s" % (name, ", ".join(sorted(language(name)["patterns"]))))
    return 0


def load(args):
    """(section, baseline path, findings, provenance, sites skipped as test code)
    from the config — KeyError naming what is missing."""
    config = quality_config.load(args.config)
    section = config.section(SECTION)
    baseline_path = config.path(config.get(SECTION, "baseline"))
    if not section.get("languages") and not section.get("patterns"):
        raise KeyError("%s: \"%s\" names no \"languages\" and no \"patterns\" — nothing to look for" % (config.file, SECTION))
    measured = ratchet.provenance("escapes", "1", {k: section[k] for k in sorted(section) if k != "baseline"})
    found, skipped = findings(section, config)
    made = census.accepted(config)   # a tool's output a person accepted (check-exclusions.py) is not judged
    found = [f for f in found if f.file not in made]
    return section, baseline_path, found, measured, skipped


def main():
    parser = argparse.ArgumentParser(description="fail on a new site where the code opts out of a check")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--list-languages", action="store_true", help="print the built-in pattern sets and exit")
    ratchet.add_only_argument(parser)
    ratchet.add_tighten_argument(parser)
    ratchet.add_strict_argument(parser)
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    if args.list_languages:
        return list_languages()
    try:
        section, baseline_path, found, measured, skipped = load(args)
    except KeyError as problem:
        print("FAIL: %s" % problem.args[0], file=sys.stderr)
        return 2

    if args.write_baseline:
        ratchet.write(baseline_path, found, measured)
        print("baseline written: %d escape site(s) accepted" % len(found))
        return 0

    entries, stored = ratchet.read(baseline_path)
    untouched = ratchet.outside(entries, args.only)
    found, entries = ratchet.restrict(found, entries, args.only)
    verdict = ratchet.judge(found, entries, ["count"], stored, measured, same=same_site)
    def with_count(v):
        return "%s%s" % (v.get("escape", "?"), " x%d" % v["count"] if v.get("count", 1) > 1 else "")
    gate = ratchet.Gate(
        noun="escape site(s)",
        over="where the code opts out of a type check, a lint rule, a test or an error",
        fix="Fix what the escape hides: give the value its real type, make the test pass or delete it, handle the "
            "error. Accepting a new escape into the baseline is a policy decision for a person — see quality/README.md.",
        remedy="quality/bin/check-escapes.py --tighten",
        show=with_count, site="line")
    ok_line = "OK: %d escape site(s) in the tree, all %d in the baseline%s" % (
        len(found), len(found), " (%d in tests skipped)" % skipped if skipped else "")
    return ratchet.report(verdict, gate, len(entries), ok_line, quiet=args.quiet, strict=args.strict,
                          tighten=args.tighten, baseline=(baseline_path, measured, untouched))


if __name__ == "__main__":
    sys.exit(main())

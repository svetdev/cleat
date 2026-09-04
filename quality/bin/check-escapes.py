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

The site's identity is its file and the text of its line, so editing the file
above it does not unbaseline it. The same line twice in one file is one entry
with a count, and that count ratchets too.

Everything that names the project is the `escapes` section of quality.json:

  "escapes": {
    "roots":     ["src", "tests"],                  # trees read (default: the config's directory)
    "languages": ["python", "typescript"],          # which built-in pattern sets apply
    "skip_dirs": ["node_modules", "vendor"],        # directory names pruned (defaults below apply too)
    "exclude":   ["*.test.ts"],                     # file globs left unread (a test tree that shares a root)
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
from extractors import patterns

SECTION = "escapes"

# Per language: the file suffixes it lives in, and the escapes worth a site each.
# A pattern is a regex over the raw text — comments included, since most escapes
# are comments — anchored loosely enough to survive spacing.
LANGUAGES = {
    "python": {
        "suffixes": [".py"],
        "patterns": {
            "type ignore": r"#\s*type:\s*ignore",
            "noqa": r"#\s*noqa\b",
            "no cover": r"#\s*pragma:\s*no cover",
            "skipped test": r"pytest\.mark\.skip|pytest\.skip\(|unittest\.skip|@skip\b",
            "bare except": r"^\s*except\s*:",
        },
    },
    "typescript": {
        "suffixes": [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"],
        "patterns": {
            "any": r":\s*any\b|\bas\s+any\b|<any>",
            "ts-ignore": r"@ts-(?:ignore|expect-error|nocheck)",
            "eslint-disable": r"eslint-disable",
            "non-null assertion": r"[\w)\]]!\.",
            "skipped test": r"\b(?:it|test|describe)\.(?:skip|only)\(|\bx(?:it|test|describe)\(",
        },
    },
    "javascript": {"alias": "typescript"},
    "swift": {
        "suffixes": [".swift"],
        "patterns": {
            "force try": r"\btry!",
            "force cast": r"\bas!",
            "force unwrap": r"[\w)\]]!(?:\.|\s*[,;)\]]|$)",
            "swiftlint:disable": r"swiftlint:disable",
            "unchecked Sendable": r"@unchecked\s+Sendable",
            "skipped test": r"\bXCTSkip|\bthrow\s+XCTSkip",
        },
    },
    "rust": {
        "suffixes": [".rs"],
        "patterns": {
            "unwrap": r"\.unwrap\(\)",
            "expect": r"\.expect\(",
            "unsafe": r"\bunsafe\s*\{",
            "allow": r"#!?\[allow\(",
            "todo": r"\b(?:todo|unimplemented)!\(",
            "skipped test": r"#\[ignore\b",
        },
    },
    "kotlin": {
        "suffixes": [".kt", ".kts"],
        "patterns": {
            "not-null assertion": r"!!",
            "suppress": r"@Suppress\(",
            "skipped test": r"@(?:Ignore|Disabled)\b",
        },
    },
    "java": {
        "suffixes": [".java"],
        "patterns": {
            "suppress warnings": r"@SuppressWarnings\(",
            "skipped test": r"@(?:Ignore|Disabled)\b",
        },
    },
    "go": {
        "suffixes": [".go"],
        "patterns": {
            "nolint": r"//\s*nolint",
            "skipped test": r"\bt\.Skip(?:Now|f)?\(",
        },
    },
    "ruby": {
        "suffixes": [".rb"],
        "patterns": {
            "rubocop:disable": r"rubocop:disable",
            "skipped test": r"\bskip\b|\bxit\b|\bpending\b",
        },
    },
    "shell": {
        "suffixes": [".sh", ".bash", ".zsh"],
        "patterns": {
            "errors ignored": r"\|\|\s*true\b|^\s*set\s+\+e\b",
            "shellcheck disable": r"shellcheck\s+disable",
        },
    },
}

DEFAULT_SKIP_DIRS = [".git", "node_modules", "vendor", "build", ".build", "dist", "target", "__pycache__",
                     ".venv", "venv", "DerivedData", "Pods", "coverage", ".next", "out", "fixtures"]


def language(name):
    """A language's suffixes and patterns, following an alias."""
    spec = LANGUAGES.get(name)
    if spec is None:
        raise KeyError("no built-in escape patterns for \"%s\" — one of: %s" % (name, ", ".join(sorted(LANGUAGES))))
    return LANGUAGES[spec["alias"]] if "alias" in spec else spec


def _collect(seen, roots, suffixes, regexes, skip, exclude, repo_root, skip_rust_tests, skipped):
    """Record every site; a site inside an inline Rust test module is counted in
    `skipped` instead when `skip_rust_tests` is on."""
    ranges = {}
    for site in patterns.sites(patterns.files(roots, suffixes, skip, exclude), regexes, repo_root):
        rel, line = site[0], site[1]
        if skip_rust_tests and rel.endswith(".rs"):
            if rel not in ranges:
                ranges[rel] = patterns.rust_test_ranges(os.path.join(repo_root, rel))
            if patterns.in_ranges(ranges[rel], line):
                skipped[0] += 1
                continue
        _record(seen, *site)


def findings(section, config):
    """Every escape site under the configured roots, one Finding per (file, line text),
    with how many times that line carries it."""
    roots = config.paths(section.get("roots", ["."]))
    skip = set(DEFAULT_SKIP_DIRS) | set(section.get("skip_dirs", []))
    exclude = section.get("exclude", [])
    skip_tests = section.get("skip_rust_tests", True)
    languages = [language(name) for name in section.get("languages", [])]
    seen, skipped = {}, [0]
    for spec in languages:
        _collect(seen, roots, spec["suffixes"], spec["patterns"], skip, exclude, config.root, skip_tests, skipped)
    if section.get("patterns"):
        suffixes = sorted({s for spec in languages for s in spec["suffixes"]}) or [""]
        _collect(seen, roots, suffixes, section["patterns"], skip, exclude, config.root, skip_tests, skipped)
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


def list_languages():
    for name in sorted(LANGUAGES):
        print("%-11s %s" % (name, ", ".join(sorted(language(name)["patterns"]))))
    return 0


def load(args):
    """(section, baseline path, findings, provenance, sites skipped as inline Rust tests)
    from the config — KeyError naming what is missing."""
    config = quality_config.load(args.config)
    section = config.section(SECTION)
    baseline_path = config.path(config.get(SECTION, "baseline"))
    if not section.get("languages") and not section.get("patterns"):
        raise KeyError("%s: \"%s\" names no \"languages\" and no \"patterns\" — nothing to look for" % (config.file, SECTION))
    measured = ratchet.provenance("escapes", "1", {k: section[k] for k in sorted(section) if k != "baseline"})
    found, skipped = findings(section, config)
    return section, baseline_path, found, measured, skipped


def main():
    parser = argparse.ArgumentParser(description="fail on a new site where the code opts out of a check")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--list-languages", action="store_true", help="print the built-in pattern sets and exit")
    ratchet.add_only_argument(parser)
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
    found, entries = ratchet.restrict(found, entries, args.only)
    verdict = ratchet.judge(found, entries, ["count"], stored, measured)
    def with_count(v):
        return "%s%s" % (v.get("escape", "?"), " x%d" % v["count"] if v.get("count", 1) > 1 else "")
    gate = ratchet.Gate(
        noun="escape site(s)",
        over="where the code opts out of a type check, a lint rule, a test or an error",
        fix="Fix what the escape hides: give the value its real type, make the test pass or delete it, handle the "
            "error. Accepting a new escape into the baseline is a policy decision for a person — see quality/README.md.",
        remedy="quality/bin/check-escapes.py --write-baseline",
        show=with_count)
    ok_line = "OK: %d escape site(s) in the tree, all %d in the baseline%s" % (
        len(found), len(found), " (%d in inline Rust tests skipped)" % skipped if skipped else "")
    return ratchet.report(verdict, gate, len(entries), ok_line, quiet=args.quiet, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())

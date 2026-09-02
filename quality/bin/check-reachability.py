#!/usr/bin/env python3
"""check-reachability — fail on a file that nothing constructs.

A service can sit in the tree, compile, be unit-tested, and be reached by
nothing the program runs. It is finished work that does nothing, and the
next agent will read it as the way things are done. This sweeps the roots,
takes every file matching `pattern`, and fails on one that no other swept
file references — by declared type name for Swift (`references:
"identifiers"`), by import for a language that has them (`"imports"`), or by
a parser's declarations and identifiers for nine languages (`"ast-grep"`,
needing ast-grep installed; see extractors/references.py). Comments and strings are stripped first: prose is
not construction.

It is a ratchet: the files that were unreached on adoption day are in
`exempt`, keyed by repo-relative path with the reason (or the item that
tracks wiring them in). The success line prints the count, so the list cannot
grow quietly. An exemption naming a file that is now reached, or that no
longer exists, fails: a dead exemption is the same rot one level up.

  "reachability": {
    "roots":      ["App", "Core/Sources/Core"],   # swept for references, judged for the pattern
    "pattern":    "Services/*",                   # which files must be reached, relative to their root
    "references": "identifiers",                  # or "imports" / "ast-grep", with "language"
    "language":   "swift",
    "extensions": [".swift"],
    "skip_dirs":  [],
    "exclude":    ["**/*.test.tsx", "**/*Tests.swift"],   # root-relative globs: neither judged nor read for references
    "exempt":     {"App/Services/Dormant.swift": "tracked as #123"}
  }

  quality/bin/check-reachability.py
  quality/bin/check-reachability.py --quiet
"""

import argparse
import fnmatch
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
from extractors import references

SECTION = "reachability"


class Rules:
    def __init__(self, config):
        section = config.section(SECTION)
        self.roots = config.paths(config.get(SECTION, "roots"))
        self.pattern = config.get(SECTION, "pattern")
        self.reader = section.get("references", "identifiers")
        self.language = section.get("language", "swift")
        self.extensions = tuple(section.get("extensions", [".swift"]))
        self.skip_dirs = set(section.get("skip_dirs", []))
        # Root-relative globs of files that are neither judged nor read for references —
        # a test constructing a service does not make it reached in production.
        self.exclude = section.get("exclude", [])
        self.exempt = config.get(SECTION, "exempt", {})
        if self.reader not in ("identifiers", "imports", "ast-grep"):
            raise KeyError('%s: "reachability.references" must be "identifiers", "imports" or "ast-grep"; got %r' % (config.file, self.reader))
        known = {"imports": references.IMPORT_RES, "ast-grep": references.ASTGREP}.get(self.reader, {self.language: 1})
        if self.language not in known:
            raise KeyError("no %s reader for %r — one of: %s" % (self.reader, self.language, ", ".join(sorted(known))))


def excluded(rules, path, root):
    rel = os.path.relpath(path, root)
    return any(fnmatch.fnmatch(rel, glob) for glob in rules.exclude)


def judged_files(rules):
    """Every swept file matching the pattern and no exclude, as absolute paths."""
    out = []
    for root in rules.roots:
        suffixes = rules.extensions if rules.reader == "identifiers" else references.SUFFIXES.get(rules.language, rules.extensions)
        for path in references.source_files(root, rules.skip_dirs, suffixes):
            if fnmatch.fnmatch(os.path.relpath(path, root), rules.pattern) and not excluded(rules, path, root):
                out.append(os.path.abspath(path))
    return sorted(set(out))


def read_references(rules, root):
    if rules.reader == "identifiers":
        code, owner = references.build_owner_index(rules.roots, rules.skip_dirs, rules.extensions)
        return references.identifier_references(root, code, owner, rules.skip_dirs, rules.extensions)
    reader = references.import_references if rules.reader == "imports" else references.astgrep_references
    return reader(root, rules.roots, rules.language, rules.skip_dirs)


def reached_files(rules):
    """Every file some other swept, non-excluded file references."""
    targets = set()
    for root in rules.roots:
        for path, _line, _name, target, _root in read_references(rules, root):
            if not excluded(rules, path, root):
                targets.add(os.path.abspath(target))
    return targets


def declared(path):
    with open(path, errors="replace") as handle:
        return references.DECL_RE.findall(references.strip_code(handle.read()))


def judge(rules, config):
    """(unreached, dead exemptions, judged count, exempt count) — paths repo-relative."""
    judged = judged_files(rules)
    reached = reached_files(rules)
    def rel(p):
        return os.path.relpath(p, config.root)
    unreached = [rel(p) for p in judged if p not in reached and rel(p) not in rules.exempt]
    known = {rel(p) for p in judged}
    dead = [(path, "now reached" if os.path.abspath(config.path(path)) in reached else "no such judged file")
            for path in sorted(rules.exempt) if path not in known or os.path.abspath(config.path(path)) in reached]
    return unreached, dead, len(judged), len([p for p in judged if rel(p) in rules.exempt])


def print_unreached(unreached, rules, config):
    print("FAIL: %d file(s) matching %s that nothing references — built, and reached by nothing:" % (len(unreached), rules.pattern))
    for path in unreached:
        names = declared(config.path(path)) if rules.reader == "identifiers" else []
        print("  %s%s" % (path, " (declares %s)" % ", ".join(names) if names else ""))
    print("Wire it into the code that should use it, or delete it. Exempting it is a decision for a person, "
          "with the reason or the tracking item beside it under \"reachability.exempt\".")


def print_dead(dead):
    print("FAIL: %d exempt entr%s name%s a file that is reached or gone — delete the entry:"
          % (len(dead), "y" if len(dead) == 1 else "ies", "s" if len(dead) == 1 else ""))
    for path, why in dead:
        print("  %s — %s" % (path, why))


def main():
    parser = argparse.ArgumentParser(description="fail on a file that nothing constructs")
    parser.add_argument("--quiet", action="store_true")
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    config = quality_config.load(args.config)
    try:
        rules = Rules(config)
        unreached, dead, judged, exempt = judge(rules, config)
    except (KeyError, references.ToolError) as problem:
        print("FAIL: %s" % (problem.args[0] if problem.args else problem), file=sys.stderr)
        return 2
    if unreached:
        print_unreached(unreached, rules, config)
    if dead:
        print_dead(dead)
    if unreached or dead:
        return 1
    if not args.quiet:
        print("OK: %d file(s) matching %s, all reached (%d exempt)" % (judged, rules.pattern, exempt))
    return 0


if __name__ == "__main__":
    sys.exit(main())

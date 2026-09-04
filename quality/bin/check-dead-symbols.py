#!/usr/bin/env python3
"""check-dead-symbols — report the functions and types that nothing references.

Agents do not delete. A helper gets rewritten beside the old one, a class is
replaced and its predecessor stays, and the file-level reachability gate sees
a file that is still imported. This reads every declaration and every
identifier through ast-grep's grammar (nine languages; see
extractors/references.py) and lists each declared name that appears nowhere
but at its own declaration, across every swept file — tests included, unless
`exclude` leaves them out.

It reports by default. "Nothing references this" is cheap to check and
expensive to be sure of: an exported API is unreferenced by design, a
framework may call by name, reflection hides callers. So the first weeks are a
list a person reads, with `exempt` growing reasons, and `"enforcement":
"block"` comes when the list is quiet.

  "dead_symbols": {
    "roots":       ["src"],
    "language":    "typescript",
    "exclude":     ["*.test.ts", "*/generated/*"],
    "ignore":      ["^main$", "^test_", "^use[A-Z]"],          # names never reported (defaults below apply too)
    "exempt":      {"src/api.ts:createClient": "public API"},   # "file:name" → the reason
    "enforcement": "report"                                     # or "block"
  }

  quality/bin/check-dead-symbols.py
  quality/bin/check-dead-symbols.py --quiet     # nothing on an empty report
"""

import argparse
import fnmatch
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
from extractors import references

SECTION = "dead_symbols"
DEFAULT_IGNORE = [r"^main$", r"^_?_init_?_?$", r"^setUp$", r"^tearDown$", r"^test", r"^Test", r"^[a-z]+$"]


class Rules:
    def __init__(self, config):
        section = config.section(SECTION)
        self.roots = config.paths(config.get(SECTION, "roots"))
        self.language = config.get(SECTION, "language")
        self.exclude = section.get("exclude", [])
        self.ignore = [re.compile(p) for p in DEFAULT_IGNORE + section.get("ignore", [])]
        self.exempt = section.get("exempt", {})
        self.block = section.get("enforcement", "report") == "block"
        if self.language not in references.ASTGREP:
            raise KeyError("no ast-grep reader for %r — one of: %s" % (self.language, ", ".join(sorted(references.ASTGREP))))


def kept(path, root, exclude):
    rel = os.path.relpath(path, root)
    return not any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(os.path.basename(rel), g) for g in exclude)


def _swept(rules, root, kinds):
    return [m for m in references.astgrep_scan(root, rules.language, kinds) if kept(m[0], root, rules.exclude)]


def occurrences(rules):
    """({name: count of identifier tokens across every swept file}, [(file, line, name)] declared)."""
    decl_kinds, ident_kinds = references.ASTGREP[rules.language]
    counts, declared = {}, []
    for root in rules.roots:
        idents = _swept(rules, root, ident_kinds)
        for _path, _line, _s, _e, text in idents:
            counts[text] = counts.get(text, 0) + 1
        declared += references.declared_symbols(_swept(rules, root, decl_kinds), idents)
    return counts, declared


def dead_among(rules, config):
    """[(repo-relative file, line, name)] declared and referenced nowhere else, minus the
    ignored and the exempt; and the exempt entries that are no longer dead."""
    counts, declared = occurrences(rules)
    dead, live_exempt = [], []
    for path, line, name in sorted(set(declared)):
        rel = os.path.relpath(path, config.root)
        key = "%s:%s" % (rel, name)
        is_dead = counts.get(name, 0) <= 1 and not any(p.search(name) for p in rules.ignore)
        if is_dead and key not in rules.exempt:
            dead.append((rel, line, name))
        elif not is_dead and key in rules.exempt:
            live_exempt.append(key)
    return dead, live_exempt


def print_dead(dead, block):
    print("%s: %d declared symbol(s) referenced nowhere in the swept files — replaced and never deleted, or an API nothing calls:"
          % ("FAIL" if block else "REPORT", len(dead)))
    for rel, line, name in dead[:40]:
        print("  %s:%d  %s" % (rel, line, name))
    print("Delete what is dead. An entry point, a public API or a name a framework calls goes under \"exempt\" with the reason.")


def print_live_exempt(live_exempt):
    many = len(live_exempt) != 1
    print("NOTE: %d exempt entr%s now referenced — delete the entry: %s" % (len(live_exempt), "ies are" if many else "y is", ", ".join(live_exempt)))


def report(rules, config, dead, live_exempt, quiet):
    if dead:
        print_dead(dead, rules.block)
    if live_exempt:
        print_live_exempt(live_exempt)
    if not dead and not quiet:
        roots = ", ".join(os.path.relpath(r, config.root) for r in rules.roots)
        print("OK: every declared symbol under %s is referenced somewhere (%d exempt)" % (roots, len(rules.exempt)))
    return 1 if dead and rules.block else 0


def main():
    parser = argparse.ArgumentParser(description="report the functions and types nothing references")
    parser.add_argument("--quiet", action="store_true")
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    config = quality_config.load(args.config)
    try:
        rules = Rules(config)
        dead, live_exempt = dead_among(rules, config)
    except (KeyError, references.ToolError) as problem:
        print("FAIL: %s" % (problem.args[0] if problem.args else problem), file=sys.stderr)
        return 2
    return report(rules, config, dead, live_exempt, args.quiet)


if __name__ == "__main__":
    sys.exit(main())

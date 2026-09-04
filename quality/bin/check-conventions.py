#!/usr/bin/env python3
"""check-conventions — fail on a new site that breaks one of the project's own rules,
and say at the site what to do instead.

A convention in a document persuades; an agent drops it forty thousand tokens
later. A convention as a check arrives exactly when it is relevant, at the
line, with the message that names the right call. This is the gate for the
rules only this project has: "import the client, not the vendor SDK", "no
direct SQL outside repositories/", "no console.log under src/". Each rule is a
regex over the raw text of the files it applies to, and every site it matches
is a finding keyed by file and line text — ratcheted like escapes, so the
sites that exist on adoption day are accepted once and a new one fails.

  "conventions": {
    "rules": [
      {"name": "vendor sdk", "pattern": "^\\s*(?:from|import)\\s+vendor_sdk\\b",
       "roots": ["src"], "extensions": [".py"], "exclude": ["*/clients/*"],
       "message": "Import the client from lib/client; only clients/ touches the vendor SDK."},
      {"name": "console.log", "pattern": "console\\.log\\(", "roots": ["src"], "languages": ["typescript"],
       "message": "Use the logger from lib/log."}
    ],
    "baseline": "quality/conventions-baseline.json"
  }

  quality/bin/check-conventions.py
  quality/bin/check-conventions.py --write-baseline
  quality/bin/check-conventions.py --strict
"""

import argparse
import importlib.util
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import quality_config
import ratchet
from extractors import patterns

_spec = importlib.util.spec_from_file_location("check_escapes", os.path.join(HERE, "check-escapes.py"))
check_escapes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_escapes)

SECTION = "conventions"
CODE_SUFFIXES = tuple(s for spec in check_escapes.LANGUAGES.values() if "suffixes" in spec for s in spec["suffixes"])


def suffixes_for(rule):
    if rule.get("extensions"):
        return tuple(rule["extensions"])
    if rule.get("languages"):
        return tuple(s for name in rule["languages"] for s in check_escapes.language(name)["suffixes"])
    return CODE_SUFFIXES


def sites_of(rule, config):
    """(repo-relative file, line, line text) for every match of one rule."""
    if not rule.get("name") or not rule.get("pattern") or not rule.get("message"):
        raise KeyError("%s: every \"conventions.rules\" entry needs \"name\", \"pattern\" and \"message\"; got %r"
                       % (config.file, rule.get("name")))
    roots = config.paths(rule.get("roots", ["."]))
    skip = set(check_escapes.DEFAULT_SKIP_DIRS) | set(rule.get("skip_dirs", []))
    files = patterns.files(roots, suffixes_for(rule), skip, rule.get("exclude", []))
    return [(rel, line, text) for rel, line, text, _ in patterns.sites(files, {rule["name"]: rule["pattern"]}, config.root)]


def findings(rules, config):
    """One Finding per (file, line text), carrying the rule and how often the line matched."""
    seen = {}
    for rule in rules:
        for rel, line, text in sites_of(rule, config):
            first_line, name, count = seen.get((rel, text), (line, rule["name"], 0))
            seen[(rel, text)] = (first_line, name, count + 1)
    out = [ratchet.Finding(rel, line, text, {"rule": name, "count": count}) for (rel, text), (line, name, count) in seen.items()]
    out.sort(key=lambda f: (f.file, f.line))
    return out


def fix_for(verdict, rules):
    """The failing rules' messages, one per line — what the agent reads at the site."""
    broken = {f.values.get("rule") for f in verdict.new} | {f.values.get("rule") for f, _ in verdict.worsened}
    messages = {r["name"]: r["message"] for r in rules}
    lines = ["%s — %s" % (name, messages.get(name, "")) for name in sorted(n for n in broken if n)]
    return "\n".join(lines) + "\nAccepting a site into the baseline is a policy decision for a person — see quality/README.md."


def main():
    parser = argparse.ArgumentParser(description="fail on a new site that breaks one of the project's rules")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    ratchet.add_only_argument(parser)
    ratchet.add_strict_argument(parser)
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    config = quality_config.load(args.config)
    try:
        section = config.section(SECTION)
        rules = config.get(SECTION, "rules")
        baseline_path = config.path(config.get(SECTION, "baseline"))
        found = findings(rules, config)
    except KeyError as problem:
        print("FAIL: %s" % problem.args[0], file=sys.stderr)
        return 2
    measured = ratchet.provenance("conventions", "1", {k: section[k] for k in sorted(section) if k != "baseline"})
    if args.write_baseline:
        ratchet.write(baseline_path, found, measured)
        print("baseline written: %d site(s) accepted across %d rule(s)" % (len(found), len(rules)))
        return 0
    entries, stored = ratchet.read(baseline_path)
    found, entries = ratchet.restrict(found, entries, args.only)
    verdict = ratchet.judge(found, entries, ["count"], stored, measured)
    gate = ratchet.Gate(
        noun="site(s)", over="breaking a convention of this project",
        fix=fix_for(verdict, rules),
        remedy="quality/bin/check-conventions.py --write-baseline",
        show=lambda v: "%s%s" % (v.get("rule", "?"), " x%d" % v["count"] if v.get("count", 1) > 1 else ""))
    ok_line = "OK: %d convention site(s) in the tree, all %d in the baseline" % (len(found), len(found))
    return ratchet.report(verdict, gate, len(entries), ok_line, quiet=args.quiet, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())

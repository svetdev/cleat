#!/usr/bin/env python3
"""check-sarif — fail on a new result from any scanner that writes SARIF.

The project runs whatever scanner it likes — a linter, a dead-code finder, a
security tool, a duplication scanner — and points this at the report. Every
result becomes a site: file plus rule plus message, so a shifted line still
matches. The baseline records what the scanner reported on adoption day, and
a result not in it fails. One gate per report; several reports are several
gates.

  "sarif": [
    {"name": "semgrep", "report": "reports/semgrep.sarif", "baseline": "quality/semgrep-baseline.json"},
    {"name": "eslint",  "report": "reports/eslint-*.sarif", "baseline": "quality/eslint-baseline.json"}
  ]

  quality/bin/check-sarif.py --gate semgrep
  quality/bin/check-sarif.py --gate semgrep --write-baseline
  quality/bin/check-sarif.py --gate semgrep --strict
  quality/bin/check-sarif.py --report FILE --baseline FILE   # fully flagged (the tests use this)
"""

import argparse
import glob
import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
import ratchet
from extractors import sarif

SECTION = "sarif"


def findings(report_path, base_dir):
    with open(report_path) as handle:
        report = json.load(handle)
    seen = {}
    for rel, line, rule, message in sarif.results(report, base_dir):
        key = (rel, "%s: %s" % (rule, message))
        first_line, count = seen.get(key, (line, 0))
        seen[key] = (first_line, count + 1)
    out = [ratchet.Finding(rel, line, text, {"count": count}) for (rel, text), (line, count) in seen.items()]
    out.sort(key=lambda f: (f.file, f.line))
    return out


def newest_report(config, entry):
    matches = sorted(glob.glob(config.path(entry["report"])), key=os.path.getmtime)
    if not matches:
        raise KeyError("no SARIF report matches %s — run the scanner first" % entry["report"])
    return matches[-1]


def locate(args):
    """(report path, baseline path, base dir, gate name, config-ish dict for provenance)."""
    if args.report and args.baseline:
        base = os.path.abspath(args.repo) if args.repo else os.getcwd()
        return args.report, args.baseline, base, args.gate or "sarif", {"report": args.report}
    config = quality_config.load(args.config)
    entry = config.entry(SECTION, args.gate)
    spec = {k: v for k, v in entry.items() if k != "baseline"}
    return newest_report(config, entry), config.path(entry["baseline"]), config.root, entry.get("name") or "sarif", spec


def main():
    parser = argparse.ArgumentParser(description="fail on a new SARIF result")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--gate", help="which entry of the sarif list")
    parser.add_argument("--report", help="a SARIF file to judge (default: the gate's report glob, newest match)")
    parser.add_argument("--baseline", help="the ratchet file (default: the gate's baseline)")
    parser.add_argument("--repo", help="paths are relative to this (default: the directory of quality.json)")
    ratchet.add_strict_argument(parser)
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    try:
        report_path, baseline_path, base_dir, name, spec = locate(args)
        found = findings(report_path, base_dir)
    except (KeyError, OSError, ValueError) as problem:
        print("FAIL: %s" % (problem.args[0] if problem.args else problem), file=sys.stderr)
        return 2
    measured = ratchet.provenance("sarif:%s" % name, None, spec)
    if args.write_baseline:
        ratchet.write(baseline_path, found, measured)
        print("baseline written: %d result site(s) accepted from %s" % (len(found), report_path))
        return 0
    entries, stored = ratchet.read(baseline_path)
    verdict = ratchet.judge(found, entries, ["count"], stored, measured)
    gate = ratchet.Gate(
        noun="%s result(s)" % name, over="the scanner reported",
        fix="Fix what each result names — the scanner's message says what. Accepting a result into the "
            "baseline is a policy decision for a person — see quality/README.md.",
        remedy="quality/bin/check-sarif.py --gate %s --write-baseline" % name,
        show=lambda v: "x%d" % v["count"] if v.get("count", 1) > 1 else "",
        brief=lambda v: "x%d" % v.get("count", 1) if v.get("count", 1) > 1 else "")
    ok_line = "OK: %d %s result site(s), all %d in the baseline — read from %s" % (len(found), name, len(found), report_path)
    return ratchet.report(verdict, gate, len(entries), ok_line, quiet=args.quiet, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())

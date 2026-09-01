#!/usr/bin/env python3
"""check-complexity-lizard — the complexity ratchet for the stacks SwiftLint does not read.

The same gate as check-complexity.sh — a production function over the
cyclomatic ceiling or the length ceiling fails — with `lizard` as the reader,
which parses Rust, TypeScript/TSX, JavaScript, Python and more. It is a
ratchet: the functions over the gate when it was adopted are in the baseline
file, keyed by file and the text of their declaration line so a shifted line
still matches, and only a new one fails. The success line prints the counts;
a baseline entry that matched nothing is noted so it can be dropped.

Everything that names the project is the `complexity_lizard` section of
quality.json (walked up to from the working directory, or `--config PATH`):

  "complexity_lizard": {
    "sources":   ["apps/api/src", "apps/web/src"],   # roots lizard reads
    "languages": ["rust", "typescript"],             # lizard -l values
    "exclude":   ["*.test.ts", "*.test.tsx"],         # lizard -x globs
    "exclude_except": ["apps/api/src/test-runner.ts"],  # production paths an exclude glob would
                                                          # otherwise drop by name; judged with no exclude
    "skip_rust_tests": true,                          # drop `#[cfg(test)]` modules (see lizard_reader.py)
    "ceilings":  {"cc": 8, "lines": 60},              # the gate
    "baseline":  "quality/complexity-baseline.json"   # the ratchet
  }

  quality/bin/check-complexity-lizard.py
  quality/bin/check-complexity-lizard.py --quiet
  quality/bin/check-complexity-lizard.py --write-baseline   # accept what is over the gate today
  quality/bin/check-complexity-lizard.py --csv FILE         # judge a saved lizard CSV (the tests use this)
"""

import argparse
import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lizard_reader  # noqa: E402
import quality_config  # noqa: E402

SECTION = "complexity_lizard"


def declaration_text(path, line):
    try:
        with open(path, errors="replace") as handle:
            lines = handle.read().split("\n")
        return lines[line - 1].strip() if 0 < line <= len(lines) else ""
    except OSError:
        return ""


def judge(functions, cc_ceiling, line_ceiling, repo):
    """[(repo-relative file, line, text, cc, length)] for every function over either ceiling."""
    repo = os.path.realpath(repo)
    over = []
    for f in functions:
        if f.cc > cc_ceiling or f.length > line_ceiling:
            over.append((os.path.relpath(f.path, repo), f.line, declaration_text(f.path, f.line), f.cc, f.length))
    over.sort(key=lambda o: (-max(o[3] / cc_ceiling, o[4] / line_ceiling), o[0], o[1]))
    return over


def main():
    parser = argparse.ArgumentParser(description="complexity ratchet over lizard's reading of the configured sources")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--csv", help="a saved lizard --csv output to judge instead of running lizard")
    parser.add_argument("--repo", help="paths are reported relative to this (default: the directory of quality.json)")
    quality_config.add_config_argument(parser)
    args = parser.parse_args()

    config = quality_config.load(args.config)
    try:
        sources = config.paths(config.get(SECTION, "sources"))
        languages = config.get(SECTION, "languages")
        excludes = config.section(SECTION).get("exclude", [])
        exclude_except = config.paths(config.section(SECTION).get("exclude_except", []))
        skip_tests = config.section(SECTION).get("skip_rust_tests", True)
        ceilings = config.get(SECTION, "ceilings")
        baseline_path = config.path(config.get(SECTION, "baseline"))
        cc_ceiling, line_ceiling = int(ceilings["cc"]), int(ceilings["lines"])
    except KeyError as problem:
        print("FAIL: %s" % problem.args[0], file=sys.stderr)
        return 2

    try:
        if args.csv:
            with open(args.csv) as handle:
                text = handle.read()
        else:
            text = lizard_reader.run_lizard(sources, languages, excludes, exclude_except)
    except lizard_reader.LizardError as problem:
        print("FAIL: %s" % problem, file=sys.stderr)
        return 2

    functions, skipped = lizard_reader.functions_from_csv(text, skip_rust_tests=skip_tests)
    repo = os.path.abspath(args.repo) if args.repo else config.root
    over = judge(functions, cc_ceiling, line_ceiling, repo)

    if args.write_baseline:
        with open(baseline_path, "w") as handle:
            json.dump([{"file": f, "text": t, "cc": cc, "lines": n} for f, _, t, cc, n in over], handle, indent=1)
            handle.write("\n")
        print("baseline written: %d function(s) over the gate (cyclomatic > %d or body > %d lines)"
              % (len(over), cc_ceiling, line_ceiling))
        return 0

    baseline = []
    if os.path.isfile(baseline_path):
        with open(baseline_path) as handle:
            baseline = json.load(handle)
    known = {(e["file"], e["text"]) for e in baseline}
    over_keys = {(o[0], o[2]) for o in over}
    stale = [e for e in baseline if (e["file"], e["text"]) not in over_keys]
    new = [o for o in over if (o[0], o[2]) not in known]
    remedy = "quality/bin/check-complexity-lizard.py --write-baseline"

    if new:
        print("FAIL: %d production function(s) over the complexity gate (cyclomatic > %d or body > %d lines), beyond the %d the baseline holds:"
              % (len(new), cc_ceiling, line_ceiling, len(baseline)))
        for f, line, text, cc, n in new:
            print("  %s:%d  cc %d, %d lines  %s" % (f, line, cc, n, text[:70]))
        print("Split the function, or — on purpose, with the reason in the commit — add it to the baseline: %s" % remedy)
        print_stale(stale, remedy)
        return 1
    if not args.quiet:
        print("OK: %d functions judged (%d inline tests skipped), %d over the gate, all %d in the baseline"
              % (len(functions), skipped, len(over), len(over)))
    print_stale(stale, remedy)
    return 0


def print_stale(stale, remedy):
    if not stale:
        return
    print("NOTE: %d baseline entr%s matched nothing this run — already split, renamed or deleted:"
          % (len(stale), "y" if len(stale) == 1 else "ies"))
    for e in stale[:20]:
        print("  %s  cc %s, %s lines  %s" % (e["file"], e["cc"], e["lines"], e["text"][:70]))
    if len(stale) > 20:
        print("  … and %d more" % (len(stale) - 20))
    print("Drop what's fixed: %s" % remedy)


if __name__ == "__main__":
    sys.exit(main())

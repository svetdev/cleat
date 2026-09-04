#!/usr/bin/env python3
"""check-changed-coverage — fail when the lines you changed are not tested.

Coverage as a repository-wide percentage answers a question nobody asked. The
question against an agent is: did it test what it just wrote? This reads the
lines changed against the base (see extractors/changed.py), keeps the ones
the coverage report calls executable, and fails when fewer than `minimum` of
them ran. A change with fewer than `min_lines` executable lines is not judged
at all — two lines cannot fail on rounding. No baseline: the scope is the
change itself.

The report is LCOV or Cobertura (see extractors/coverage.py), whichever the
stack's test command writes.

  "changed_coverage": {
    "report":    "coverage/lcov.info",   # a path or glob; the newest match is read
    "minimum":   0.8,                    # share of changed executable lines that must have run
    "min_lines": 20,                     # fewer changed executable lines than this: not judged
    "base_ref":  "origin/main"           # optional
  }

  quality/bin/check-changed-coverage.py
  quality/bin/check-changed-coverage.py --report FILE --base REF --minimum 0.9 --min-lines 10
"""

import argparse
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
from extractors import changed, coverage

SECTION = "changed_coverage"
# A changed file the report does not know is worth a note only when it is code.
CODE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".swift", ".rs", ".kt", ".kts", ".java", ".go",
                 ".rb", ".c", ".cc", ".cpp", ".h", ".hpp", ".m", ".mm", ".cs", ".php", ".scala", ".ex", ".exs")


def ranges(numbers):
    """'3-5, 9' for {3, 4, 5, 9}."""
    out, run = [], []
    for n in sorted(numbers):
        if run and n == run[-1] + 1:
            run.append(n)
        else:
            if run:
                out.append(run)
            run = [n]
    if run:
        out.append(run)
    return ", ".join("%d-%d" % (r[0], r[-1]) if len(r) > 1 else str(r[0]) for r in out)


def judge(changed_lines, lines_by_file, root):
    """({rel: uncovered set}, judged count, covered count, changed files the report
    does not know)."""
    uncovered, judged, covered, unknown = {}, 0, 0, []
    for rel, numbers in changed_lines.items():
        hits = lines_by_file.get(os.path.realpath(os.path.join(root, rel)))
        if hits is None:
            if rel.endswith(CODE_SUFFIXES):
                unknown.append(rel)
            continue
        executable = numbers & set(hits)
        judged += len(executable)
        missed = {n for n in executable if hits[n] == 0}
        covered += len(executable) - len(missed)
        if missed:
            uncovered[rel] = missed
    return uncovered, judged, covered, unknown


def report_path_for(args, section, config):
    pattern = args.report or section.get("report")
    if not pattern:
        raise KeyError("%s: \"%s\" has no \"report\" — see quality.example.json" % (config.file, SECTION))
    path = args.report or coverage.newest(config.path(pattern))
    if path is None or not os.path.isfile(path):
        raise KeyError("no coverage report matches %s — run the tests with coverage first" % pattern)
    return path


def first(flag, fallback):
    return flag if flag is not None else fallback


def settings(args):
    """(report path, minimum, min_lines, base ref, config) — KeyError naming what is missing."""
    config = quality_config.load(args.config)
    section = config.data.get(SECTION, {})
    report_path = report_path_for(args, section, config)
    minimum = float(first(args.minimum, section.get("minimum", 0.8)))
    min_lines = int(first(args.min_lines, section.get("min_lines", 20)))
    base = changed.base_ref(config.root, first(args.base, section.get("base_ref")))
    return report_path, minimum, min_lines, base, config


def verdict(uncovered, judged, covered, unknown, minimum, min_lines, report_path, quiet):
    if judged < min_lines:
        if not quiet:
            print("OK: %d changed executable line(s), under the %d it takes to judge — read from %s" % (judged, min_lines, report_path))
        return 0
    share = covered / judged
    if share >= minimum:
        if not quiet:
            print("OK: %d of %d changed executable line(s) ran (%.0f%%, minimum %.0f%%) — read from %s"
                  % (covered, judged, share * 100, minimum * 100, report_path))
        return 0
    print("FAIL: %d of %d changed executable line(s) ran (%.0f%%), under the %.0f%% minimum — read from %s"
          % (covered, judged, share * 100, minimum * 100, report_path))
    for rel in sorted(uncovered)[:20]:
        print("  %s: lines %s" % (rel, ranges(uncovered[rel])))
    if unknown:
        print("  (%d changed file(s) the report does not know were not judged)" % len(unknown))
    print("Write the test that exercises the change — the lines above are what it has to reach.")
    return 1


def main():
    parser = argparse.ArgumentParser(description="fail when the changed lines are not covered")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--report", help="the LCOV or Cobertura report (default: changed_coverage.report, newest match)")
    parser.add_argument("--base", help="the ref changed lines are measured against")
    parser.add_argument("--minimum", type=float)
    parser.add_argument("--min-lines", type=int)
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    try:
        report_path, minimum, min_lines, base, config = settings(args)
    except KeyError as problem:
        print("FAIL: %s" % problem.args[0], file=sys.stderr)
        return 2
    lines = coverage.line_coverage(coverage.read(report_path, config.root))
    uncovered, judged, covered, unknown = judge(changed.changed_lines(config.root, base), lines, config.root)
    return verdict(uncovered, judged, covered, unknown, minimum, min_lines, report_path, args.quiet)


if __name__ == "__main__":
    sys.exit(main())

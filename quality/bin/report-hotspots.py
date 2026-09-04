#!/usr/bin/env python3
"""report-hotspots — rank every measured function by churn × complexity.

Complexity alone says a function is hard to change; it does not say anyone is
changing it. A function nobody touches can sit at cc 20 for years and cost
nothing more than what check-complexity.py already charges it. The one that
costs is the function that is both complex *and* hot — someone comes back to
it every few days, and every one of those visits pays the complexity tax. This
multiplies the two and ranks the result, so refactoring effort goes where it
actually pays for itself, rather than at whatever is most complex today.

This is a report, not a gate: nothing here fails a build, and there is no
baseline. Complexity comes from whatever the project measures it with: lizard
when `crap.complexity.tool` or the `complexity` section says so,
else the same SwiftLint pass check-crap.py runs (`lint_complexities`,
threshold 1, over `crap.sources`) — the readers are reused, not duplicated. Churn is the number of commits that
touched a function's file in the last N days (`hotspots.window_days` in
quality.json, default 90), from `git log --since --name-only`.

Churn is per file, not per function, so a simple function in a hot file can
outscore a complex function in a quiet one. A floor (`hotspots.min_cc` in
quality.json, default 0 — nothing held back) drops any function under it from
the ranking entirely, so the report only ever surfaces functions the
complexity gate itself would flag.

  quality/bin/report-hotspots.py                     # top 20, over crap.sources, last 90 days
  quality/bin/report-hotspots.py --top 5
  quality/bin/report-hotspots.py --window-days 30
  quality/bin/report-hotspots.py --min-cc 8
  quality/bin/report-hotspots.py --config PATH
  quality/bin/report-hotspots.py --lint F --sources DIR --repo DIR --window-days N   # the tests use these
  quality/bin/report-hotspots.py --lizard-csv F --repo DIR                           # from a saved lizard run
  quality/bin/report-hotspots.py --sources DIR --tool lizard --languages python       # lizard, no config needed
"""

import argparse
import json
import os
import sys
from shutil import which

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extractors import churn, complexity
import quality_config


SECTION = "hotspots"


class GateError(Exception):
    """A reason the report cannot run today; printed as FAIL, exit 2."""


class Settings:
    """The project facts: each from its flag when given, else from quality.json,
    opened the first time a value has to come from it."""

    def __init__(self, explicit_config):
        self.explicit = explicit_config
        self._config = None

    @property
    def config(self):
        if self._config is None:
            self._config = quality_config.load(self.explicit)
        return self._config


def window_days_for(args, settings):
    """--window-days when given; else `hotspots.window_days` in whichever quality.json is
    reachable; else 90. A run given every other flag opens no quality.json to find this —
    the absent section is not an error, it just means the default applies."""
    if args.window_days is not None:
        return args.window_days
    path = settings.explicit or quality_config.find()
    if path is None:
        return 90
    try:
        section = settings.config.section(SECTION)
    except KeyError:
        return 90
    return int(section.get("window_days", 90))


def min_cc_for(args, settings):
    """--min-cc when given; else `hotspots.min_cc` in whichever quality.json is reachable;
    else 0 — a function under the floor is dropped from the ranking, not shown at 0."""
    if args.min_cc is not None:
        return args.min_cc
    path = settings.explicit or quality_config.find()
    if path is None:
        return 0
    try:
        section = settings.config.section(SECTION)
    except KeyError:
        return 0
    return int(section.get("min_cc", 0))


def lizard_spec(settings):
    """The lizard configuration to measure with, when the project measures with lizard:
    `crap.complexity` when its `tool` is lizard, else the `complexity` section (or its
    old name) when it does; None when neither is there or no quality.json is in reach."""
    if settings.explicit is None and quality_config.find() is None:
        return None
    data = settings.config.data
    crap = data.get("crap")
    if isinstance(crap, dict) and isinstance(crap.get("complexity"), dict) and crap["complexity"].get("tool") == "lizard":
        return crap["complexity"]
    for key in ("complexity", "complexity_lizard"):
        section = data.get(key)
        if isinstance(section, dict) and (section.get("tool") == "lizard" or section.get("languages")):
            return section
    return None


def complexities_from_lizard(settings, spec, roots=None):
    """lizard over the configured `spec` — its sources, or `roots` when --sources
    overrides them — as check-complexity.py runs it."""
    try:
        return complexity.lizard_complexities(roots or settings.config.paths(spec["sources"]), spec["languages"],
                                              spec.get("exclude", []), spec.get("skip_rust_tests", True))
    except complexity.ToolError as problem:
        raise GateError(str(problem))


def complexities_from_swiftlint(args, settings):
    """SwiftLint over --sources, else crap.sources."""
    if not which("swiftlint"):
        raise GateError("swiftlint is not installed — brew install swiftlint")
    roots = [os.path.abspath(s) for s in args.sources] if args.sources else \
        settings.config.paths(settings.config.get("crap", "sources"))
    return complexity.swiftlint_complexities(roots)


def _saved_report(args):
    """Complexities from a saved report named by a flag, or None."""
    if args.lint:
        with open(args.lint) as handle:
            return complexity.complexities_from_swiftlint(json.load(handle))
    if args.lizard_csv:
        with open(args.lizard_csv) as handle:
            functions, _ = complexity.functions_from_csv(handle.read())
        return complexity.complexities(functions)
    return None


def complexities_for(args, settings):
    """From --lint (SwiftLint json) or --lizard-csv (a saved lizard run); else the reader
    the project measures with — lizard when `complexity.tool` (or `crap.complexity.tool`)
    says so, over --sources when given — or lizard over --sources with --tool lizard and
    --languages; else SwiftLint."""
    saved = _saved_report(args)
    if saved is not None:
        return saved
    roots = [os.path.abspath(s) for s in args.sources] if args.sources else None
    if args.tool == "lizard" and args.languages:
        spec = {"languages": args.languages, "sources": []}
    else:
        spec = lizard_spec(settings)
    if spec is not None and args.tool != "swiftlint":
        return complexities_from_lizard(settings, spec, roots)
    return complexities_from_swiftlint(args, settings)


def repo_for(args, settings):
    return os.path.abspath(args.repo) if args.repo else settings.config.root


def churn_by_file(repo, window_days):
    """{abs realpath: commit count} over the window — the churn extractor, with its
    failure reported the way this report reports one."""
    try:
        return churn.commits_by_file(repo, window_days)
    except churn.ChurnError as problem:
        raise GateError(str(problem))


def rank(complexities, churn, repo, min_cc=0):
    """[(file relative to `repo`, line, text, cc, commits, score)], hottest first, for every
    function at or above `min_cc` — one under the floor is not ranked at all."""
    repo = os.path.realpath(repo)
    rows = []
    for (path, line), cc in complexities.items():
        if cc < min_cc:
            continue
        commits = churn.get(path, 0)
        score = commits * cc
        rows.append((os.path.relpath(path, repo), line, complexity.declaration_text(path, line), cc, commits, score))
    rows.sort(key=lambda r: (-r[5], r[0], r[1]))
    return rows


def main():
    parser = argparse.ArgumentParser(description="rank every measured function by churn (commits) x complexity")
    parser.add_argument("--top", type=int, default=20, help="how many to print (default: 20)")
    parser.add_argument("--window-days", type=int, help="the churn window (default: hotspots.window_days, else 90)")
    parser.add_argument("--min-cc", type=int, help="drop functions under this complexity from the ranking (default: hotspots.min_cc, else 0)")
    parser.add_argument("--repo", help="the git root git log runs in, and paths are reported relative to (default: the directory of quality.json)")
    parser.add_argument("--sources", nargs="+", help="trees to lint (default: crap.sources)")
    parser.add_argument("--tool", choices=["lizard", "swiftlint"], help="which reader measures (default: what the config says, else swiftlint)")
    parser.add_argument("--languages", nargs="+", help="lizard -l values, for --tool lizard without a config")
    parser.add_argument("--lint", help="a SwiftLint json report, skipping a real lint run (tests use this)")
    parser.add_argument("--lizard-csv", help="a saved lizard --csv run, skipping a real lizard run")
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    try:
        return report(args, Settings(args.config))
    except (GateError, KeyError) as problem:
        print("FAIL: %s" % (problem.args[0] if problem.args else problem), file=sys.stderr)
        return 2


def report(args, settings):
    if args.top <= 0:
        raise GateError("--top must be positive")
    window_days = window_days_for(args, settings)
    min_cc = min_cc_for(args, settings)
    complexities = complexities_for(args, settings)
    repo = repo_for(args, settings)
    churn = churn_by_file(repo, window_days)
    rows = rank(complexities, churn, repo, min_cc)
    shown = rows[:args.top]
    hot = sum(1 for (path, _line) in complexities if churn.get(path, 0) > 0)
    held_back = len(complexities) - len(rows)
    floor_clause = ", %d held back by the cc %d floor" % (held_back, min_cc) if min_cc > 0 else ""
    print("%d function(s) measured, %d touched in the last %d day(s)%s, top %d by churn x complexity:"
          % (len(complexities), hot, window_days, floor_clause, len(shown)))
    for f, line, text, cc, commits, score in shown:
        print("  %s:%d  hotspot %d (churn %d x cc %d)  %s" % (f, line, score, commits, cc, text[:70]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

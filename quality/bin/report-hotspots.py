#!/usr/bin/env python3
"""report-hotspots — rank every measured function by churn × complexity.

Complexity alone says a function is hard to change; it does not say anyone is
changing it. A function nobody touches can sit at cc 20 for years and cost
nothing more than what check-complexity.sh already charges it. The one that
costs is the function that is both complex *and* hot — someone comes back to
it every few days, and every one of those visits pays the complexity tax. This
multiplies the two and ranks the result, so refactoring effort goes where it
actually pays for itself, rather than at whatever is most complex today.

This is a report, not a gate: nothing here fails a build, and there is no
baseline. Complexity comes from the same SwiftLint pass check-crap.py already
runs (`lint_complexities`, threshold 1, over `crap.sources` in quality.json) —
reused here rather than run a second time. Churn is the number of commits that
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
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from shutil import which

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import quality_config  # noqa: E402

_spec = importlib.util.spec_from_file_location("check_crap", os.path.join(HERE, "check-crap.py"))
check_crap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_crap)

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


def complexities_for(args, settings):
    """From --lint, else SwiftLint over --sources, else crap.sources."""
    if args.lint:
        with open(args.lint) as handle:
            return check_crap.complexities_from_lint(json.load(handle))
    if not which("swiftlint"):
        raise GateError("swiftlint is not installed — brew install swiftlint")
    roots = [os.path.abspath(s) for s in args.sources] if args.sources else \
        settings.config.paths(settings.config.get("crap", "sources"))
    return check_crap.lint_complexities(roots)


def repo_for(args, settings):
    return os.path.abspath(args.repo) if args.repo else settings.config.root


def churn_by_file(repo, window_days):
    """{abs realpath: commit count} for every file touched by a commit in the last
    `window_days` days, from `git log --name-only` — a filename appears at most once per
    commit in that output, so counting lines is counting commits."""
    proc = subprocess.run(
        ["git", "-C", repo, "log", "--since=%d days ago" % window_days, "--name-only", "--pretty=format:"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise GateError("git log failed in %s: %s" % (repo, proc.stderr.strip()))
    counts = {}
    for line in proc.stdout.splitlines():
        name = line.strip()
        if not name:
            continue
        path = os.path.realpath(os.path.join(repo, name))
        counts[path] = counts.get(path, 0) + 1
    return counts


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
        rows.append((os.path.relpath(path, repo), line, check_crap.declaration_text(path, line), cc, commits, score))
    rows.sort(key=lambda r: (-r[5], r[0], r[1]))
    return rows


def main():
    parser = argparse.ArgumentParser(description="rank every measured function by churn (commits) x complexity")
    parser.add_argument("--top", type=int, default=20, help="how many to print (default: 20)")
    parser.add_argument("--window-days", type=int, help="the churn window (default: hotspots.window_days, else 90)")
    parser.add_argument("--min-cc", type=int, help="drop functions under this complexity from the ranking (default: hotspots.min_cc, else 0)")
    parser.add_argument("--repo", help="the git root git log runs in, and paths are reported relative to (default: the directory of quality.json)")
    parser.add_argument("--sources", nargs="+", help="trees to lint (default: crap.sources)")
    parser.add_argument("--lint", help="a SwiftLint json report, skipping a real lint run (tests use this)")
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

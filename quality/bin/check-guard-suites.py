#!/usr/bin/env python3
"""check-guard-suites — fail if a guard suite on disk is named by no entry in
the preflight array.

A guard suite — a `test-*.py` or `test-*.sh` under one of the roots
`"guard_suites"."roots"` names — is only worth anything if something runs it.
Nothing compared the suites on disk against the `PREFLIGHT` array in
the test runner before this existed, and nine had drifted out of it: two under
`quality/tests/` and seven under a tooling directory. Each sits on disk, each asserts something real, and
none runs anywhere the suite measures — the same "green because it never ran"
shape this repository's other checks were written to end, one level up: not a
check that stopped judging, but a whole judge nobody wired in.

This reads `PREFLIGHT` out of `scripts/run-unit-suite.sh` as text rather than
importing or running it — the array is bash, not Python, and the point is to
read what a preflight run would read, not to re-derive it. Every entry is a
quoted command; the first word of each is the path it runs, whatever flags
follow (`--quiet`, and the like). That set of paths is compared against every
`test-*.py` and `test-*.sh` file found anywhere beneath each swept root, not
only at its top level — a suite one directory deeper is exactly the shape of
drift this check exists to end. A root listed both as a parent and as one of
its own children (`scripts` and `scripts/tools`, which is how
`quality.json` spells it today) yields each nested suite once: suites are
deduplicated by resolved path, not by which root's walk happened to find them
first.

It is a ratchet, the same shape as `"exempt_services"` in
`check-reachability.py`: a suite outside the preflight is named in
`"exempt"`, keyed by its path relative to the repository root, with the
reason it is there — where the backlog already tracks the work of wiring it
in, the item id is the reason. The success line says how many are exempt, so
the list cannot grow quietly. An `"exempt"` entry naming a path no swept root
has is itself a failure — the tree moved past it rather than someone taking
it off the list — reported the same way a stale `"exempt_services"` entry is.

An entry can also go stale the other way: the check above only ever asks
whether `checked` — the suites not on the exempt list — are named by
`PREFLIGHT`, so an exempt suite that has since been wired in is never looked
at again. The exemption stands forever, counted in the success line's exempt
figure as though the suite still ran nowhere, and would silently exempt it
again if it later dropped back out of `PREFLIGHT`. So every `"exempt"` entry
is also checked against `PREFLIGHT` directly, and one now named there is
reported — by path and by the reason recorded for it — the same way a stale
entry is. The fix is the same too: delete the entry, or, if it should stay
exempt on purpose, update the reason to say why.

Some files named `test-*.py`/`test-*.sh` under a swept root are not guard
suites at all — `scripts/tools/test-move.py` is a tool that moves a
test between targets, not a test itself, and it has a guard suite
of its own. `"not_suites"`, keyed the same way as `"exempt"`, says so: a path
listed there is dropped from the sweep before `"exempt"` is even consulted,
so it is counted in neither the checked figure nor the exempt one. The
success line reports that count on its own, so a file cannot be quietly
recategorized to dodge either list. A `"not_suites"` entry naming a path no
swept root has is a failure, reported the same way a stale `"exempt"` entry
is. The key is optional — a `quality.json` without it judges exactly as one
with an empty map.

Exits non-zero and lists the offenders if any non-exempt, non-`"not_suites"`
guard suite is named by no `PREFLIGHT` entry, any `"exempt"` or `"not_suites"`
entry names a path no swept root has, or any `"exempt"` entry names a suite
`PREFLIGHT` now runs. Exits 2 when no
`quality.json` can be found, the one found lacks the `"guard_suites"`
section, or `scripts/run-unit-suite.sh` has no `PREFLIGHT` array to read.
No third-party dependencies (Python 3 stdlib only).

Usage
  quality/bin/check-guard-suites.py               # check the roots quality.json names
  quality/bin/check-guard-suites.py --quiet       # only print on failure
  quality/bin/check-guard-suites.py --config PATH # run under another quality.json
  quality/bin/check-guard-suites.py --list        # print each swept suite's repo-relative
                                                   # path, one per line (not_suites dropped,
                                                   # exempt suites included), and exit 0

quality.json
  "guard_suites": {
    "preflight": "scripts/run-unit-suite.sh",   the script whose PREFLIGHT array is read
    "patterns": ["test-*.py", "test-*.sh"],      what a suite is named like (this is the default)
    "roots": ["quality/tests", "scripts", "scripts/tools"],
    "exempt": {                                 suites run nowhere standing, keyed repo-relative
      "quality/tests/test-dormant.py": "tracked as <item id>"
    },
    "not_suites": {                             test-*.py/test-*.sh files that are not suites
      "scripts/tools/test-move.py": "the tool that moves a test between targets"
    }
  }
  Paths are relative to the directory quality.json is in.
"""

import argparse
import fnmatch
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config

SECTION = "guard_suites"

# The array itself, non-greedy so a later `(`...`)` pair in the file is not
# swallowed, and its quoted entries -- each one a full command, path first.
PREFLIGHT_RE = re.compile(r"PREFLIGHT=\((.*?)\n\)", re.DOTALL)
ENTRY_RE = re.compile(r'"([^"]*)"')

# A guard suite's filename, wherever beneath a swept root it sits.
# The filename shapes a guard suite takes, unless `"guard_suites"."patterns"` says
# otherwise — a pytest tree is `test_*.py`, a Go one `*_test.go`.
DEFAULT_PATTERNS = ["test-*.py", "test-*.sh"]

# Pruned wherever it appears while walking a root -- a compiled cache is not a
# file this project put there on purpose.
EXCLUDED_DIRS = {"__pycache__"}

# A suite found beneath a swept root: `path`, its path relative to the
# repository root -- the spelling `"exempt"` is keyed by, and the one a FAIL
# line reports.
Suite = collections.namedtuple("Suite", ["path"])


def repo_path(suite):
    """A suite's path relative to the repository root -- the spelling
    `"exempt"` is keyed by, and the one a FAIL line reports."""
    return suite.path


class Settings:
    def __init__(self, config):
        self.config_path = config.file
        self.repo = config.root
        self.preflight_script = config.path(config.get(SECTION, "preflight"))
        self.roots = config.get(SECTION, "roots")
        self.root_dirs = config.paths(self.roots)
        self.exempt = config.get(SECTION, "exempt")
        self.not_suites = config.get(SECTION, "not_suites", {})
        self.patterns = config.section(SECTION).get("patterns", DEFAULT_PATTERNS)


def parse_preflight(text):
    """The path each `PREFLIGHT` entry runs, in the order the array lists them.

    Raises ValueError naming what is missing when the array itself, or an
    entry inside it, cannot be read -- a preflight run this cannot parse is
    not evidence anything is wired up.
    """
    match = PREFLIGHT_RE.search(text)
    if not match:
        raise ValueError("no PREFLIGHT array found")
    entries = ENTRY_RE.findall(match.group(1))
    if not entries:
        raise ValueError("PREFLIGHT array is empty")
    return [entry.split()[0] for entry in entries]


def _is_suite_file(path, name, patterns=DEFAULT_PATTERNS):
    """A filename under a swept root that is a guard suite -- matches one of
    `patterns` and exists as a regular file."""
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns) and os.path.isfile(path)


def swept_suites(settings):
    """Every `test-*.py`/`test-*.sh` file anywhere beneath each of `"roots"`,
    as `Suite`s, root by root in the order `"roots"` lists them, each named
    once even when one root sits inside another."""
    found = []
    seen = set()
    for directory in settings.root_dirs:
        for dirpath, dirnames, filenames in os.walk(directory):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                if not _is_suite_file(path, name, settings.patterns):
                    continue
                real = os.path.realpath(path)
                if real in seen:
                    continue
                seen.add(real)
                found.append(Suite(os.path.relpath(path, settings.repo)))
    return found


def suites_to_run(settings):
    """Every swept suite with `"not_suites"` dropped, in sweep order -- what
    `--list` prints and what `judge()` checks against PREFLIGHT before
    `"exempt"` is even consulted. Exempt suites stay in: they are suites, and
    running them is the point of `--list`."""
    swept = swept_suites(settings)
    not_suite_paths = set(settings.not_suites)
    return [suite for suite in swept if repo_path(suite) not in not_suite_paths]


def display(path, repo):
    """Name a file the way a reader can click it: repo-relative, or absolute."""
    relative = os.path.relpath(path, repo)
    return path if relative.startswith(os.pardir) else relative


def report_unlisted(unlisted, shown_script, shown_config):
    print(
        f"FAIL: {len(unlisted)} guard suite(s) run nowhere {shown_script}'s "
        "PREFLIGHT array names.\n"
        "A suite the preflight never runs is a check whose own test could go\n"
        "red forever without anyone finding out.\n",
        file=sys.stderr,
    )
    for suite in unlisted:
        print(f"    {repo_path(suite)}", file=sys.stderr)
    print(
        f"\nAdd it to PREFLIGHT in {shown_script} (the test before the check it\n"
        f'guards), or add it to "exempt" in {shown_config} with the reason and\n'
        "the item that tracks wiring it up. Do not leave it silently unrun.",
        file=sys.stderr,
    )


def report_stale_entries(stale_entries, shown_config, key):
    """The shared shape for a stale `"exempt"` or `"not_suites"` entry -- a
    path neither list can have been judged against, since no swept root has
    it."""
    print(
        f'FAIL: {len(stale_entries)} "{key}" entry(ies) in {shown_config} '
        "name a path\n"
        "no swept root has.\n"
        "A stale entry cannot have been judged, and would silently apply to\n"
        "whatever file is later added at that same path.\n",
        file=sys.stderr,
    )
    for path, reason in stale_entries:
        print(f"    {path}: {reason}", file=sys.stderr)
    print(
        "\nDelete the entry -- the tree has moved on, not the reason it was added.\n"
        "Do not re-add a file just to keep the entry valid.",
        file=sys.stderr,
    )


def report_retired_exemptions(retired_exemptions, shown_script, shown_config):
    """A `"exempt"` entry naming a suite `PREFLIGHT` runs now -- the mirror of
    `report_unlisted`: the exemption was true once and nothing re-checked it."""
    print(
        f'FAIL: {len(retired_exemptions)} "exempt" entry(ies) in {shown_config} '
        "name a suite\n"
        f"{shown_script}'s PREFLIGHT array now runs.\n"
        "An exemption is a claim that nothing runs the suite; once something does,\n"
        "leaving the entry in place counts it as unwired forever and would silently\n"
        "exempt it again if it later dropped back out of PREFLIGHT.\n",
        file=sys.stderr,
    )
    for path, reason in retired_exemptions:
        print(f"    {path}: {reason}", file=sys.stderr)
    print(
        "\nDelete the entry -- the suite is wired in now, not still unwired. If it\n"
        "should stay exempt anyway, the reason needs updating to say why.",
        file=sys.stderr,
    )


def _stale_entries(entries, known):
    """The `("exempt"|"not_suites")` entries naming a path no swept root has
    -- neither list can have been judged against a path that is not there."""
    return [(path, entries[path]) for path in sorted(entries) if path not in known]


def _retired_exemptions(exempt, known, not_suite_paths, entry_paths):
    """The mirror of `unlisted`: `checked` is asked whether PREFLIGHT names
    it, but nothing ever asks the exempt entries the same question, so one
    wired in since it was exempted stays counted as unwired forever. A path
    that is stale, or dropped as a not-suite, is not a suite PREFLIGHT could
    have picked up, so neither is a candidate here."""
    return [
        (path, exempt[path])
        for path in sorted(exempt)
        if path in known and path not in not_suite_paths and path in entry_paths
    ]


def judge(settings):
    """(checked, exempt_count, not_suite_count, unlisted, stale_exemptions,
    stale_not_suites, retired_exemptions) for one run.

    `checked`, `exempt_count` and `not_suite_count` are what the OK line
    needs; `unlisted`, `stale_exemptions`, `stale_not_suites` and
    `retired_exemptions` are what a FAIL line reports. Raises (OSError,
    ValueError) reading or parsing the preflight script -- main() turns
    either into an exit 2, the same way a missing config key does.
    """
    with open(settings.preflight_script) as handle:
        entry_paths = set(parse_preflight(handle.read()))

    swept = swept_suites(settings)
    known = {repo_path(suite) for suite in swept}

    stale_not_suites = _stale_entries(settings.not_suites, known)

    # Dropped before "exempt" is even consulted, so a not-suite is counted in
    # neither the checked figure nor the exempt one.
    not_suite_paths = set(settings.not_suites)
    suites = [suite for suite in swept if repo_path(suite) not in not_suite_paths]

    stale_exemptions = _stale_entries(settings.exempt, known)

    checked = [suite for suite in suites if repo_path(suite) not in settings.exempt]
    unlisted = [suite for suite in checked if repo_path(suite) not in entry_paths]

    retired_exemptions = _retired_exemptions(
        settings.exempt, known, not_suite_paths, entry_paths
    )

    return (
        checked,
        len(suites) - len(checked),
        len(swept) - len(suites),
        unlisted,
        stale_exemptions,
        stale_not_suites,
        retired_exemptions,
    )


def _report_failures(sections):
    """Print each non-empty (rows, reporter) pair from `sections`, a reporter
    call per pair, separated by a blank line -- the bookkeeping the FAIL half
    otherwise repeats once per report."""
    printed = False
    for rows, reporter in sections:
        if not rows:
            continue
        if printed:
            print(file=sys.stderr)
        reporter(rows)
        printed = True


def emit(
    checked,
    exempt_count,
    not_suite_count,
    unlisted,
    stale_exemptions,
    stale_not_suites,
    retired_exemptions,
    settings,
    quiet,
):
    """Print the OK or FAIL line(s) for one judge() result and return the exit
    status -- split out of main() so each stays a single, small decision."""
    shown_script = display(settings.preflight_script, settings.repo)
    shown_config = display(settings.config_path, settings.repo)

    if (
        not unlisted
        and not stale_exemptions
        and not stale_not_suites
        and not retired_exemptions
    ):
        if not quiet:
            where = ", ".join(f"{root}/" for root in settings.roots)
            print(
                f"OK: all {len(checked)} guard suite(s) under {where} are named "
                f"in {shown_script}'s PREFLIGHT array "
                f"({exempt_count} exempt, {not_suite_count} not a suite)"
            )
        return 0

    _report_failures(
        [
            (unlisted, lambda rows: report_unlisted(rows, shown_script, shown_config)),
            (
                stale_exemptions,
                lambda rows: report_stale_entries(rows, shown_config, "exempt"),
            ),
            (
                retired_exemptions,
                lambda rows: report_retired_exemptions(rows, shown_script, shown_config),
            ),
            (
                stale_not_suites,
                lambda rows: report_stale_entries(rows, shown_config, "not_suites"),
            ),
        ]
    )
    return 1


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--quiet", action="store_true", help="only print on failure")
    ap.add_argument(
        "--list",
        action="store_true",
        help="print each swept suite's repo-relative path, one per line, and exit 0",
    )
    quality_config.add_config_argument(ap)
    args = ap.parse_args()

    try:
        settings = Settings(quality_config.load(args.config))
    except KeyError as problem:
        print(f"FAIL: {problem.args[0]}", file=sys.stderr)
        return 2

    if args.list:
        for suite in suites_to_run(settings):
            print(repo_path(suite))
        return 0

    try:
        (
            checked,
            exempt_count,
            not_suite_count,
            unlisted,
            stale_exemptions,
            stale_not_suites,
            retired_exemptions,
        ) = judge(settings)
    except (OSError, ValueError) as problem:
        shown_script = display(settings.preflight_script, settings.repo)
        print(f"FAIL: {shown_script}: {problem}", file=sys.stderr)
        return 2

    return emit(
        checked,
        exempt_count,
        not_suite_count,
        unlisted,
        stale_exemptions,
        stale_not_suites,
        retired_exemptions,
        settings,
        args.quiet,
    )


if __name__ == "__main__":
    sys.exit(main())

#!/bin/bash
# check-complexity — fail on a production function more complex, or longer, than
# the gate allows, beyond the ones the baseline already names.
#
# Agents write long functions and do not notice; neither does a reviewer reading
# a diff, since the function was already long before the diff. A threshold a
# tool enforces is the one kind of rule an agent cannot soften, and it is the
# gate Uncle Bob runs over agent-written code: a function's paths are the tests
# it takes to cover it, and past a point nobody writes them.
#
# SwiftLint is the reader; which rules are on, and at what threshold, is the
# SwiftLint config the project names. It is a ratchet: the violations that stood
# when the gate was written are in the baseline file and are not reported, so a
# run reports only what is new. The success line says how many the baseline
# holds, so the list cannot grow quietly; shrinking it is
# `swiftlint lint --write-baseline` on a tree with fewer violations, which is
# what fixing one should end with.
#
# Everything that names the project comes from the "complexity" section of the
# nearest quality.json above the working directory (or the one `--config PATH`
# names):
#
#   "cwd"       the directory to run swiftlint from, relative to quality.json
#   "config"    the SwiftLint config, relative to cwd
#   "baseline"  the SwiftLint baseline, relative to cwd
#   "sources"   the trees to lint, relative to cwd
#   "tool"      which reader measures complexity; defaults to "swiftlint" when
#               absent. SwiftLint is the only reader wired — see
#               quality/STRATEGY.md for why a nesting-weighted (cognitive-style)
#               reader isn't a second option here yet. Any other value fails
#               naming it, rather than silently falling back.
#
# The gate runs from cwd with the sources named relative to it, and so must the
# write command it prints: SwiftLint matches a baseline entry by the path it
# was written with, so the two must name the tree the same way or nothing in
# the baseline matches and every old violation reads as new.
#
# A machine without swiftlint fails here and says so: a gate that skips itself
# is a gate nobody can rely on, and `brew install swiftlint` is the whole fix.
# Reads the tree, starts no build — safe while the app is running.
#
#   ./quality/bin/check-complexity.sh
#   ./quality/bin/check-complexity.sh --quiet          # only print on failure
#   ./quality/bin/check-complexity.sh --config PATH    # this quality.json, not the nearest
set -u

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
quiet=""
explicit=""
while [ $# -gt 0 ]; do
  case "$1" in
    --quiet) quiet=1 ;;
    --config) shift; explicit="${1:-}" ;;
    --config=*) explicit="${1#--config=}" ;;
    *) echo "FAIL: unknown argument '$1' — usage: check-complexity.sh [--quiet] [--config PATH]" >&2; exit 2 ;;
  esac
  shift
done

# The project's facts, one per line: cwd resolved, cwd as written, config,
# baseline, then the sources. The loader beside this script finds the file
# and refuses a missing key by name, so a fact nobody wrote is never a default.
facts=$(python3 - "$here" "$explicit" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import quality_config
cfg = quality_config.load(sys.argv[2] or None)
try:
    cwd = cfg.get("complexity", "cwd")
    config = cfg.get("complexity", "config")
    baseline = cfg.get("complexity", "baseline")
    sources = cfg.get("complexity", "sources")
    tool = cfg.get("complexity", "tool", "swiftlint")
except KeyError as problem:
    print(f"FAIL: {problem.args[0]}", file=sys.stderr)
    sys.exit(2)
if tool != "swiftlint":
    print(f'FAIL: {cfg.file}: "complexity.tool" is "{tool}" — only "swiftlint" is wired '
          '(see quality/STRATEGY.md for why a nesting-weighted reader is not a second option yet)',
          file=sys.stderr)
    sys.exit(2)
if isinstance(sources, str):
    sources = [sources]
if not sources:
    print(f'FAIL: {cfg.file}: "complexity" names no "sources" to lint', file=sys.stderr)
    sys.exit(2)
print(cfg.path(cwd))
print(cwd)
print(config)
print(baseline)
print("\n".join(sources))
PY
) || exit 2
cwd_abs=$(printf '%s\n' "$facts" | sed -n '1p')
cwd_rel=$(printf '%s\n' "$facts" | sed -n '2p')
config=$(printf '%s\n' "$facts" | sed -n '3p')
baseline=$(printf '%s\n' "$facts" | sed -n '4p')
sources=$(printf '%s\n' "$facts" | sed -n '5,$p')
# shellcheck disable=SC2086
sources_line=$(printf '%s ' $sources); sources_line=${sources_line% }

# How a path under cwd reads to someone at quality.json's directory, and the
# command that rewrites the baseline from there.
shown=""; remedy="swiftlint lint --config $config --write-baseline $baseline $sources_line"
if [ "$cwd_rel" != "." ]; then
  shown="$cwd_rel/"; remedy="cd $cwd_rel && $remedy"
fi

cd -P "$cwd_abs" 2>/dev/null || { echo "FAIL: $cwd_rel (\"cwd\" in quality.json) is not a directory" >&2; exit 2; }
cwd_abs=$(pwd -P)

if ! command -v swiftlint >/dev/null 2>&1; then
  echo "FAIL: swiftlint is not installed, so the complexity gate cannot run — brew install swiftlint" >&2
  exit 2
fi
[ -f "$config" ] || { echo "FAIL: $shown$config is missing" >&2; exit 2; }
[ -f "$baseline" ] || { echo "FAIL: $shown$baseline is missing — write it with: $remedy" >&2; exit 2; }

# How many the baseline holds: SwiftLint writes it as a JSON list of violations.
held=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$baseline" 2>/dev/null || echo "?")

# The dead-entry note: a fresh baseline, written from this run over the same
# sources, names only what still trips the gate; SwiftLint's own baseline
# compare then names what the committed baseline holds that the fresh one does
# not — a function shortened, split, renamed or deleted since the entry was
# accepted, still silently exempting whatever grows back in its place. Judged
# by whether the fresh file was written at all, not by its exit status:
# --write-baseline exits non-zero whenever the tree has error-severity
# violations, which every run here does. A swiftlint that cannot produce or
# compare one leaves this silent rather than failing the gate over it.
print_stale_note() {
  local fresh; fresh=$(mktemp -t check-complexity-fresh)
  # shellcheck disable=SC2086
  swiftlint lint --config "$config" --write-baseline "$fresh" $sources >/dev/null 2>/dev/null
  if [ ! -s "$fresh" ] || ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$fresh" >/dev/null 2>&1; then
    rm -f "$fresh"
    return 0
  fi
  local compare
  compare=$(swiftlint baseline compare "$fresh" --other-baseline "$baseline" 2>/dev/null)
  local compare_status=$?
  rm -f "$fresh"
  [ "$compare_status" -eq 0 ] || return 0
  [ -n "$compare" ] || return 0
  local count; count=$(printf '%s\n' "$compare" | grep -c .)
  local plural="ies"; [ "$count" -eq 1 ] && plural="y"
  echo "NOTE: $count baseline entr$plural matched nothing this run — already shortened, split, renamed or deleted:"
  printf '%s\n' "$compare" | sed -e "s#^$cwd_abs/##" -e 's/ (cyclomatic_complexity)$//' -e 's/ (function_body_length)$//' -e 's/^/  /'
  echo "Drop what no longer applies, re-accept what still is over the gate: $remedy"
}

errors=$(mktemp -t check-complexity)
# shellcheck disable=SC2086
output=$(swiftlint lint --quiet --config "$config" --baseline "$baseline" $sources 2>"$errors")
status=$?
if [ -n "$output" ]; then
  count=$(printf '%s\n' "$output" | grep -c .)
  echo "FAIL: $count production function(s) over the complexity gate (cyclomatic > 8 or body > 60 lines), beyond the $held the baseline holds:"
  printf '%s\n' "$output" | sed -e "s#^$cwd_abs/##" -e 's/ (cyclomatic_complexity)$//' -e 's/ (function_body_length)$//'
  echo "Split the function, or — on purpose, with the reason in the commit — add it to the baseline: $remedy"
  rm -f "$errors"
  print_stale_note
  exit 1
fi
if [ "$status" -ne 0 ]; then
  echo "FAIL: swiftlint exited $status without reporting a violation:" >&2
  cat "$errors" >&2
  rm -f "$errors"
  exit "$status"
fi
rm -f "$errors"
[ -n "$quiet" ] || echo "OK: no production function over the complexity gate beyond the $held in the baseline"
print_stale_note

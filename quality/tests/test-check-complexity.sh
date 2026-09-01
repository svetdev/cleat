#!/bin/bash
# test-check-complexity — assert the gate in quality/bin/check-complexity.sh.
#
# The gate is SwiftLint behind a baseline, and its failure mode is the usual
# one: a config that stops naming the rule, a baseline path that stops
# resolving, a swiftlint that is not there — and it prints OK while judging
# nothing, or skips and says nothing. So each case drives the script, through
# `--config`, over a throwaway root holding a quality.json and a one-function
# app tree: the function over the gate fails and is named; the same function in
# the baseline passes and the success line counts it; a missing baseline names
# the write command the config implies; a quality.json without the section
# fails naming it; with no `--config`, the nearest quality.json above the
# working directory is the one read, and none at all is a refusal; a PATH
# without swiftlint fails and names the fix rather than skipping; --quiet
# prints nothing on success. Then the dead-entry note: a baseline entry whose
# function is shortened until it no longer trips the gate is named as stale
# while the gate still passes; restored, the same baseline matches everything
# and the note is gone; and with a swiftlint that cannot write a fresh
# baseline, the gate still verdicts and simply says nothing about staleness.
# Last, the real script runs against this checkout, through the quality.json
# at its root, and must exit 0.
#
# It needs the swiftlint this machine has and starts no build, reads no process
# list and writes nothing outside a directory it makes under the user's cache
# and removes on exit — safe on its own or in the suite while the app is
# running.
#
#   ./quality/tests/test-check-complexity.sh
set -u

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../.." && pwd)
script=$root/quality/bin/check-complexity.sh
failed=0

check() {
  local name=$1 expected=$2 actual=$3
  if [ "$actual" = "$expected" ]; then
    printf '  ok    %s\n' "$name"
  else
    printf '  FAIL  %s\n          expected: %s\n          actual:   %s\n' "$name" "$expected" "$actual"
    failed=$((failed + 1))
  fi
}
check_contains() {
  local name=$1 needle=$2 haystack=$3
  case "$haystack" in
    *"$needle"*) printf '  ok    %s\n' "$name" ;;
    *) printf '  FAIL  %s\n          expected to contain: %s\n          got: %s\n' "$name" "$needle" "$haystack"; failed=$((failed + 1)) ;;
  esac
}
check_not_contains() {
  local name=$1 needle=$2 haystack=$3
  case "$haystack" in
    *"$needle"*) printf '  FAIL  %s\n          expected not to contain: %s\n          got: %s\n' "$name" "$needle" "$haystack"; failed=$((failed + 1)) ;;
    *) printf '  ok    %s\n' "$name" ;;
  esac
}

if ! command -v swiftlint >/dev/null 2>&1; then
  echo "test-check-complexity: swiftlint is not installed, so neither the gate nor its test can run — brew install swiftlint" >&2
  exit 2
fi

# Under the user's cache directory, not the system temp root: SwiftLint's
# baseline matching silently finds nothing for a tree anywhere under /private
# (the temp root, /tmp, /var/tmp — probed, all of them, physical paths and all)
# and works for the same tree under $HOME. The directory is made fresh, used
# only by this run, and removed on exit, which is what a temp directory is for.
cache="${XDG_CACHE_HOME:-$HOME/Library/Caches}"
mkdir -p "$cache"
tmp=$(mktemp -d "$cache/check-complexity.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
fixture=$tmp/repo
mkdir -p "$fixture/App/Sources"
# The gate's own rules, written into the fixture rather than copied from any
# project's config, so the test means the same thing in every checkout.
cat > "$fixture/App/.swiftlint.yml" <<'YML'
only_rules: [cyclomatic_complexity, function_body_length]
cyclomatic_complexity: {warning: 8, error: 8, ignores_case_statements: true}
function_body_length: {warning: 60, error: 60}
YML
# The project's facts, in the shape the real quality.json uses: a cwd under
# the file's directory, and the config, baseline and sources relative to it.
cat >"$fixture/quality.json" <<'JSON'
{
  "complexity": {
    "cwd": "App",
    "config": ".swiftlint.yml",
    "baseline": ".swiftlint-baseline.json",
    "sources": ["Sources"]
  }
}
JSON
# One function with nine independent branches: over the gate of eight.
cat >"$fixture/App/Sources/Knotted.swift" <<'SWIFT'
func knotted(_ a: Int) -> Int {
    var n = 0
    if a > 1 { n += 1 }
    if a > 2 { n += 1 }
    if a > 3 { n += 1 }
    if a > 4 { n += 1 }
    if a > 5 { n += 1 }
    if a > 6 { n += 1 }
    if a > 7 { n += 1 }
    if a > 8 { n += 1 }
    if a > 9 { n += 1 }
    return n
}
SWIFT

# No baseline yet: the remedy names the write command the config implies.
out=$("$script" --config "$fixture/quality.json" 2>&1); status=$?
check 'a missing baseline — fails' 2 "$status"
check_contains 'and names the write command from the config' 'write it with: cd App && swiftlint lint --config .swiftlint.yml --write-baseline .swiftlint-baseline.json Sources' "$out"

# An empty baseline is an empty list: that is the shape SwiftLint writes.
printf '[]\n' >"$fixture/App/.swiftlint-baseline.json"

out=$("$script" --config "$fixture/quality.json" 2>&1); status=$?
check 'a function over the gate, not in the baseline — fails' 1 "$status"
check_contains 'and is named with its file, relative to cwd' 'Sources/Knotted.swift' "$out"
check_contains 'and the line says what the gate is' 'cyclomatic > 8 or body > 60 lines' "$out"
check_contains 'and the remedy is the write command from the config' 'add it to the baseline: cd App && swiftlint lint --config .swiftlint.yml --write-baseline .swiftlint-baseline.json Sources' "$out"

(cd -P "$fixture/App" && swiftlint lint --quiet --config .swiftlint.yml --write-baseline .swiftlint-baseline.json Sources >/dev/null 2>&1)
out=$("$script" --config "$fixture/quality.json" 2>&1); status=$?
check 'the same function in the baseline — passes' 0 "$status"
check 'and the success line counts what the baseline holds' 'OK: no production function over the complexity gate beyond the 1 in the baseline' "$out"
out=$("$script" --quiet --config "$fixture/quality.json" 2>&1)
check '--quiet prints nothing on success' '' "$out"

# A dead baseline entry: the baselined function is shortened until the tree no
# longer produces it, but the committed baseline still names it.
cat >"$fixture/App/Sources/Knotted.swift" <<'SWIFT'
func knotted(_ a: Int) -> Int {
    var n = 0
    if a > 1 { n += 1 }
    return n
}
SWIFT
out=$("$script" --config "$fixture/quality.json" 2>&1); status=$?
check 'a baseline entry the tree no longer produces — the gate still passes' 0 "$status"
check_contains 'and is named as stale' 'NOTE: 1 baseline entry matched nothing this run' "$out"
check_contains 'naming the file it no longer matches' 'Sources/Knotted.swift' "$out"
check_contains 'with the same remedy the FAIL path composes' 'cd App && swiftlint lint --config .swiftlint.yml --write-baseline .swiftlint-baseline.json Sources' "$out"

# Restored, the baseline matches everything again: no note at all.
cat >"$fixture/App/Sources/Knotted.swift" <<'SWIFT'
func knotted(_ a: Int) -> Int {
    var n = 0
    if a > 1 { n += 1 }
    if a > 2 { n += 1 }
    if a > 3 { n += 1 }
    if a > 4 { n += 1 }
    if a > 5 { n += 1 }
    if a > 6 { n += 1 }
    if a > 7 { n += 1 }
    if a > 8 { n += 1 }
    if a > 9 { n += 1 }
    return n
}
SWIFT
out=$("$script" --config "$fixture/quality.json" 2>&1); status=$?
check 'a baseline whose every entry still matches — passes' 0 "$status"
check_not_contains 'and prints no stale note at all' 'NOTE' "$out"

# A swiftlint that cannot write a fresh baseline: the gate still reports its
# own verdict and simply says nothing about staleness, rather than failing
# over a comparison it could not make.
wrap=$tmp/wrap-write-baseline
mkdir -p "$wrap"
real_swiftlint=$(command -v swiftlint)
cat >"$wrap/swiftlint" <<SCRIPT
#!/bin/bash
for a in "\$@"; do
  [ "\$a" = "--write-baseline" ] && exit 1
done
exec "$real_swiftlint" "\$@"
SCRIPT
chmod +x "$wrap/swiftlint"
out=$(PATH="$wrap:$PATH" "$script" --config "$fixture/quality.json" 2>&1); status=$?
check 'swiftlint cannot write a fresh baseline — the gate still verdicts' 0 "$status"
check_contains 'and still prints its own success line' 'OK: no production function over the complexity gate beyond the 1 in the baseline' "$out"
check_not_contains 'and says nothing about staleness' 'NOTE' "$out"

# An unknown "tool" fails naming it, rather than silently running swiftlint
# anyway or silently doing nothing. Points at the same App/ tree the earlier
# cases already wrote a passing baseline for, so a pass here could only come
# from the tool check being skipped.
unknown=$tmp/unknown-tool.json
cat >"$unknown" <<JSON
{
  "complexity": {
    "tool": "lizard",
    "cwd": "$fixture/App",
    "config": ".swiftlint.yml",
    "baseline": ".swiftlint-baseline.json",
    "sources": ["Sources"]
  }
}
JSON
out=$("$script" --config "$unknown" 2>&1); status=$?
check 'an unknown "tool" — fails' 2 "$status"
check_contains 'and names the value' '"complexity.tool" is "lizard"' "$out"
check_contains 'and names the only one wired' 'only "swiftlint" is wired' "$out"

# Without --config: the nearest quality.json above the working directory, from
# anywhere under it — and none at all is a refusal, not a default.
out=$(cd "$fixture/App/Sources" && "$script" 2>&1); status=$?
check 'no --config — the nearest quality.json above the working directory' 0 "$status"
nowhere=$tmp/nowhere; mkdir -p "$nowhere"
out=$(cd "$nowhere" && "$script" --config "$nowhere/quality.json" 2>&1); status=$?
check 'a quality.json that does not exist — fails' 2 "$status"
check_contains 'and says which' 'quality.json' "$out"

# A quality.json without the section: refused by name, nothing linted.
printf '{"project": "Other"}\n' >"$nowhere/quality.json"
out=$(cd "$nowhere" && "$script" 2>&1); status=$?
check 'a quality.json without a "complexity" section — fails' 2 "$status"
check_contains 'and names the section' '"complexity"' "$out"

# A PATH with no swiftlint on it: the gate refuses rather than skipping.
bare=$tmp/bare; mkdir -p "$bare"
for tool in bash python3 grep sed dirname mktemp cat printf; do
  p=$(command -v "$tool") && ln -s "$p" "$bare/$tool"
done
out=$(PATH="$bare" "$script" --config "$fixture/quality.json" 2>&1); status=$?
check 'no swiftlint — fails rather than skipping' 2 "$status"
check_contains 'and names the fix' 'brew install swiftlint' "$out"

# This checkout, through the real script and the quality.json at its root —
# judged only when that quality.json configures a "complexity" section.
if python3 -c 'import json,sys; sys.exit(0 if "complexity" in json.load(open(sys.argv[1])) else 1)' "$root/quality.json" 2>/dev/null; then
  out=$(cd "$root" && "$script" 2>&1); status=$?
  check 'this checkout passes the gate' 0 "$status"
  check_contains 'and its success line names the baseline count' 'beyond the' "$out"
else
  check 'this checkout configures no "complexity" section, so it is not judged' 0 0
fi

echo
if [ "$failed" -ne 0 ]; then
  echo "test-check-complexity: $failed case(s) failed."
  exit 1
fi
echo "test-check-complexity: all cases passed."

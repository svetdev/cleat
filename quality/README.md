# quality/ — deterministic gates a project carries with it

The adoption guide is this file; the strategy — why these checks, the
adoption ladder, per-stack tool choices, and the roadmap — is `STRATEGY.md`
beside it.

Nine checks, each a ratchet: a baseline records what was over the line the
day the check was adopted, and only new debt fails. None needs the project
clean first, and each has its own test that drives it over a throwaway tree.
Everything that names a particular project is in one file, `quality.json`, at
the repository root, read by every check through the shared
`bin/quality_config.py`; the checks are generic.

| Check | What it refuses | Baseline |
|---|---|---|
| `bin/check-complexity.sh` | a function over cyclomatic 8 or 60 lines (SwiftLint) | `complexity.baseline`, SwiftLint's own format |
| `bin/check-crap.py` | a function over CRAP 8 — `cc² × (1 − coverage)³ + cc`, complexity the tests do not pay for | `crap.baseline`, keyed by file and declaration text |
| `bin/check-layering.py` | a reference from a lower layer to a higher one | `layering.exempt`, each with its reason |
| `bin/check-test-hygiene.py` | a test habit (fixed sleeps, port probes, hand-built temp dirs…) past its ceiling | the ceilings in `hygiene.habits` |
| `bin/check-doc-size.py` | a document past its word ceiling — the instructions file that keeps growing | `doc_size` |
| `bin/check-features-map.py` | a capability row citing a file that is gone, or a service nothing constructs | `features_map.exempt_services` |
| `bin/check-guard-suites.py` | a guard suite (`test-*.py`/`test-*.sh`) under a swept `guard_suites.roots` that the project's test runner names in no `PREFLIGHT` entry | `guard_suites.exempt`, keyed by path with the reason it's there |
| `bin/mutate.py` | not a gate: flips operators one at a time and reports the mutants no test kills | — |
| `bin/report-hotspots.py` | not a gate: ranks every measured function by churn × complexity, so refactoring effort goes where it actually pays for itself | — |

## Adopting it

1. Copy this directory into the repository.
2. Copy `quality.example.json` to the repository root as `quality.json` and fill
   it in — every path is relative to that file; `~` and absolute paths pass
   through. A key a check needs that the file lacks fails naming the key; there
   are no silent defaults.
3. Write the baselines once: `bin/check-complexity.sh` prints the SwiftLint
   command; `bin/check-crap.py --write-baseline` after a coverage run;
   `layering.exempt` is filled from the first run's findings, with a reason each.
4. Wire the checks into whatever runs the tests, in two places: the `test-*`
   scripts and the checks as a **preflight** (a failing check stops the run
   before a build starts), and `check-crap.py` as a **postflight** after a
   green run, since it reads that run's coverage. `bin/check-guard-suites.py`
   reads that same preflight array back, so run it alongside the others once
   it's wired: a `test-*` suite added later and never named in the preflight
   fails loudly instead of sitting idle.
5. Run `tests/run.sh` — every guard suite `quality.json` names (its own list
   comes from `bin/check-guard-suites.py --list`), from the repository root —
   roughly two minutes, not seconds.

## What stays the project's

The test runner itself. Running a suite is the one part that differs by stack
— which tool, which result bundle, what a tally line looks like — so the
template ends at the preflight/postflight contract and the runner is the
consumer's. Kiteloop's is `scripts/run-unit-suite.sh`, 1,900 lines of which
about a third read an `.xcresult`; a `cargo test` or `pytest` runner is a page.

Coverage readers are the other stack-specific piece. `check-crap.py` reads
Xcode's `xccov` report for an app target and llvm-cov's JSON export for a Swift
package; a project on another stack adds a reader returning
`{(absolute file, declaration line): coverage}` and names it in `crap`.

## The config, key by key

See `quality.example.json` — Kiteloop's own, a working example rather than a
schema. Sections: `complexity` (`tool` — defaults to `"swiftlint"`, the only
reader wired; SwiftLint's cwd, config, baseline, source roots), `crap`
(threshold, baseline, lint roots, one object per coverage
reader), `layering` (the app root, the layer order as `allowed` — each layer
names what it may reach, `null` for no restriction — and `exempt`),
`hygiene` (test roots, directories to skip, habits as pattern/ceiling/the
spelling to use instead), `doc_size` (file and word ceiling, a list),
`features_map` (the map, the roots a citation may resolve under, the roots
swept for services that must be constructed, exemptions), `mutation` (the
package and its sources), `guard_suites` (`preflight` — the script whose
`PREFLIGHT` array is read, `roots` swept for `test-*` suites, `exempt`, and
`not_suites` — a swept `test-*` file that is not a suite at all, dropped
before `exempt` is even consulted).

## Added by the Kiu adoption (2026-08-24) — to fold back upstream

The template forks the day a consumer patches it privately, so these are
written as template changes, each with its test:

- `bin/lizard_reader.py` + `bin/check-complexity-lizard.py` — the complexity
  ratchet for Rust, TypeScript and whatever else `lizard` parses; section
  `complexity_lizard`. Inline Rust `#[cfg(test)]` modules are skipped.
  `complexity_lizard.exclude_except` names production paths an `exclude` glob
  would otherwise drop by filename alone (a guard-suite exclude like
  `*test-*` also matching a production file whose name happens to contain
  "test-"): `run_lizard` reads each with a second, exclude-free pass and
  appends its rows, so only those paths are exempted — everything else the
  glob drops stays dropped.
- `check-crap.py`: `complexity.tool: "lizard"` inside a gate; an `istanbul`
  coverage reader (vitest/c8 `coverage-final.json`); readers are optional per
  gate (only the configured ones run); `crap` may be a **list of gates**, each
  with a `name`, selected with `--gate`. Flags `--lizard-csv`, `--istanbul`,
  `--web-sources`, `--gate`.
- `check-test-hygiene.py`: `hygiene.extensions` (which suffixes to count) and
  `hygiene.test_file_roots` (trees that mix production and test code, counted
  through test files only).
- `mutate.py`: `mutation.test_command` and `mutation.filter_flag` — run the
  suite through something other than `swift test` (an iOS package through
  `xcodebuild` on a simulator).
- Tests: the "this checkout" cases skip when the checkout's `quality.json`
  chose not to configure that section; `test-check-complexity.sh` writes its
  own SwiftLint rules into the fixture instead of copying a project's file;
  `test-quality-config.py` requires only the language-agnostic pair.

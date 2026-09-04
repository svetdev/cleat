# quality/ — deterministic gates a project carries with it

The adoption guide is this file; the strategy — why these checks, the adoption ladder, per-stack tool choices, and the roadmap — is `STRATEGY.md` beside it.

Nineteen checks, each a ratchet: a baseline records what was over the line the day the check was adopted; new debt fails, and so does baselined debt that got worse (`bin/ratchet.py` — the one implementation they share). None needs the project clean first, and each has its own test that drives it over a throwaway tree. Everything that names a particular project is in one file, `quality.json`, at the repository root, read by every check through the shared `bin/quality_config.py`; the checks are generic.

| Check | What it refuses | Baseline |
|---|---|---|
| `bin/check-complexity.py` | a function over cyclomatic 8 or 60 lines — lizard for most stacks, SwiftLint for Swift | `complexity.baseline`, keyed by file and declaration text, with the numbers |
| `bin/check-crap.py` | a function over CRAP 8 — `cc² × (1 − coverage)³ + cc`, complexity the tests do not pay for | `crap.baseline`, keyed by file and declaration text |
| `bin/check-layering.py` | a reference from a lower layer to a higher one — Swift by declared names, any language with imports (`references: "imports"`), or nine languages by parser (`"ast-grep"`) | `layering.exempt`, each with its reason |
| `bin/check-test-hygiene.py` | a test habit (fixed sleeps, port probes, hand-built temp dirs…) past its ceiling | the ceilings in `hygiene.habits` |
| `bin/check-doc-size.py` | a document past its word ceiling — the instructions file that keeps growing | `doc_size` |
| `bin/check-reachability.py` | a file matching a pattern (`Services/*`) that no other file references — by declared names, imports, or ast-grep | `reachability.exempt`, each with its reason |
| `bin/check-escapes.py` | a new line that opts out of a type check, a lint rule, a test or an error (`any`, `unwrap()`, `# type: ignore`, `.skip`, `\|\| true`…), keyed by site, per language | `escapes.baseline` |
| `bin/check-conventions.py` | a new site breaking one of the project's own rules — a regex, where it applies, and the message the agent reads at the site | `conventions.baseline` |
| `bin/check-dead-symbols.py` | a function or type nothing references, through ast-grep — a report by default, `enforcement: block` when the list is quiet | `dead_symbols.exempt` |
| `bin/check-duplication.py` | a copied block overlapping the lines changed against the base, and the repository's duplicated share rising — built-in finder, or a jscpd report | `duplication.baseline` (the share) |
| `bin/check-changed-coverage.py` | changed executable lines of which fewer than `minimum` ran — LCOV or Cobertura, no baseline | — |
| `bin/check-public-api.py` | a public signature removed, renamed or changed — the built-in reader per language, or `cargo public-api` / api-extractor reports | per surface, the recorded signatures |
| `bin/check-manifests.py` | a source file a generated project (`project.pbxproj`, a file list) does not name — compiled into nothing, run by nothing | `manifests[].exempt`, with reasons |
| `bin/check-inventory.py` | a directory that must not shrink (a `.sqlx` cache, snapshots) losing an entry | per directory, the recorded entries |
| `bin/check-sarif.py` | a new result from any scanner that writes SARIF, keyed by file, rule and message | per report |
| `bin/check-doc-citations.py` | a document citing a file that is not there | — |
| `bin/check-guard-suites.py` | a guard suite (`test-*.py`/`test-*.sh`) under a swept `guard_suites.roots` that the project's test runner names in no `PREFLIGHT` entry | `guard_suites.exempt`, keyed by path with the reason it's there |
| `bin/gate.py` | not a check: runs every gate the config names; `--strict` for CI, `--hook` and `--guard` for an agent's hooks, `--changed` scopes the heavy gates to the files changed against the base (the hook's default), `--stats` for what those two did (each firing is a line in `quality/.events.jsonl`, gitignored; a refused call records its target, an allowed one only the tool; `"events": false` in the config turns it off) | — |
| `bin/attach.py` | not a check: attaches all of this to a project in one command | — |
| `bin/mutate.py` | not a gate: flips operators one at a time and reports the mutants no test kills | — |
| `bin/report-hotspots.py` | not a gate: ranks every measured function by churn × complexity, so refactoring effort goes where it actually pays for itself | — |

## Adopting it

`python3 /path/to/cleat/quality/bin/attach.py --into /path/to/project` does steps 1–4 for tier 0 (and the complexity ratchet when `lizard` is installed), wires the agent hooks, and is safe to run again; `--ci` adds the workflow and CODEOWNERS. By hand:

1. Copy this directory into the repository.
2. Copy `quality.example.json` to the repository root as `quality.json` and fill it in — every path is relative to that file; `~` and absolute paths pass through. A key a check needs that the file lacks fails naming the key; there are no silent defaults.
3. Write the baselines once: each ratchet's `--write-baseline` (`check-crap.py` after a coverage run); `layering.exempt` and `reachability.exempt` are filled from the first run's findings, with a reason each.
4. Run `bin/gate.py` wherever the tests run — as a **preflight** before a build starts, `--postflight` after a green run for `check-crap.py`, which reads that run's coverage — and `bin/gate.py --strict` in CI.

## How cleat attaches

Local by default: the gates, the baselines and the agent hooks. `--git-hooks` adds a pre-push hook; `--ci` adds the workflow and CODEOWNERS when there is a reviewer or the agent pushes with your keys. Each layer stops something the others do not.

| Where | Stops | Does not stop |
|---|---|---|
| CI: `gate.py --strict --skip-missing-tools` as a required check, code-owner review on the control plane, no bypass | a merge with a failing or loosened gate; a baseline or ceiling changed without a person | nothing it can see — it is the authority |
| Agent hooks: Stop runs the gates and blocks one stop; PreToolUse refuses `--write-baseline` and edits to `quality.json`, the baselines, the gates, the hooks | the agent finishing with a red gate; the agent loosening policy mid-task | a session with different settings; a plain terminal |
| Git pre-push hook (`attach --git-hooks`) | a push with a red gate, from a human or an agent with no hook harness | `--no-verify` |

The Stop hook blocks once per stop: on the stop after that (`stop_hook_active`) it reports and lets the agent go, so an unfixable failure does not loop it; CI refuses the result. Branch protection assumes an identity that cannot approve or bypass — an agent authenticated as you can do both — so the agent should hold its own GitHub identity with contents and pull-request write only. Attach prints the ruleset command that makes the check required.

## The ratchet, precisely

Every baselined gate sorts each finding into one of five outcomes: **new** (fails), **worsened** — a recorded value went up (fails), **held**, **improved**, **stale** — the entry matched nothing. The last two mean the baseline is looser than the code; each is a NOTE with the command that tightens it, and under `--strict` a failure, so CI keeps the file exact. A baseline records what measured it (tool, version, a hash of the gate's config); a run under a different one is noted the same way. Failure output names the fix and never the accept command.

The tiers, by what a project has to have:

| Tier | Adds | Needs |
|---|---|---|
| 0 | doc-size, doc-citations, test-hygiene, escapes, duplication, guard-suites, manifests, inventory, layering and reachability by imports | nothing |
| 1 | complexity, hotspots, sarif, public-api, layering and reachability by ast-grep | `lizard` (or SwiftLint); any SARIF scanner; `ast-grep` |
| 2 | changed-coverage, crap | an LCOV or Cobertura report |
| 3 | layering and reachability by declared names | Swift |
| 4 | mutation | a per-stack tool |

## The config, key by key

See `quality.example.json` — this repository's own, a working example rather than a schema. Sections: `complexity` (`tool` — lizard or swiftlint — sources, languages, ceilings, baseline), `crap` (threshold, baseline, lint roots, one object per coverage reader), `layering` (the app root, the layer order as `allowed` — each layer names what it may reach, `null` for no restriction — and `exempt`), `hygiene` (test roots, directories to skip, habits as pattern/ceiling/the spelling to use instead), `doc_size` (file and word ceiling, a list), `escapes` (roots, `languages` from `check-escapes.py --list-languages`, extra `patterns`, `skip_dirs`, baseline), `duplication` (roots, languages, `min_lines`, baseline, optional `report.jscpd`), `changed_coverage` (report, minimum, `min_lines`), `sarif` (a list: name, report glob, baseline), `doc_citations` (a list: file, roots, extensions), `conventions` (rules: name, pattern, roots, extensions or languages, exclude, message; baseline), `dead_symbols` (roots, language, exclude, ignore, exempt, enforcement), `public_api` (a list: name, language and roots — or a `report` — and baseline), `manifests` (a list: file, roots, extensions, exempt), `inventory` (a list: name, path, pattern, baseline), `gates` (a list of named gates — `check`, `with` — for the same check over different facts), `reachability` (roots, the `pattern` of files that must be reached, how references are read, exemptions), `mutation` (the package and its sources), `guard_suites` (`preflight` — the script whose `PREFLIGHT` array is read, `roots` swept for `test-*` suites, `exempt`, and `not_suites` — a swept `test-*` file that is not a suite at all, dropped before `exempt` is even consulted).

## Added by the second adoption (2026-08-24) — to fold back upstream

The template forks the day a consumer patches it privately, so these are written as template changes, each with its test:

- `check-complexity.py` reads with lizard for Rust, TypeScript and whatever else `lizard` parses (`complexity.tool: "lizard"`). Inline Rust `#[cfg(test)]` modules are skipped. `complexity.exclude_except` names production paths an `exclude` glob would otherwise drop by filename alone (a guard-suite exclude like `*test-*` also matching a production file whose name happens to contain "test-"): those paths get a second, exclude-free pass, so only they are exempted — everything else the glob drops stays dropped.
- `check-crap.py`: `complexity.tool: "lizard"` inside a gate; an `istanbul` coverage reader (vitest/c8 `coverage-final.json`); readers are optional per gate (only the configured ones run); `crap` may be a **list of gates**, each with a `name`, selected with `--gate`. Flags `--lizard-csv`, `--istanbul`, `--web-sources`, `--gate`.
- `check-test-hygiene.py`: `hygiene.extensions` (which suffixes to count) and `hygiene.test_file_roots` (trees that mix production and test code, counted through test files only).
- `mutate.py`: `mutation.test_command` and `mutation.filter_flag` — run the suite through something other than `swift test` (an iOS package through `xcodebuild` on a simulator).
- Tests: the "this checkout" cases skip when the checkout's `quality.json` chose not to configure that section; `test-quality-config.py` requires only the language-agnostic pair.

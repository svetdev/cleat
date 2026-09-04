# Changelog

Newest first. A line per gate or behavior change; policy changes to this repository's own baselines are in the commits that made them.

## Unreleased

- Mutation: a filtered (narrow) suite run that executed no tests is inconclusive, not survival; the full suite decides. It made CI flaky on Linux, where the filter sometimes matched nothing.
- #3: identical `(file, text)` findings are matched by the values they still share with an entry, then by nearest recorded line (entries now record their line), so a function inserted between two baselined twins is the new one, not a neighbour.
- #4: the Rust test skip is the `#[cfg(test)]` item's own braces, not the rest of the file; production code appended below the test module is judged by complexity, escapes and duplication.
- Ratchet: two baselined findings in one file with an identical declaration line (`def check(` twice) no longer collide on the baseline key and compare against each other, which failed the gate on an unedited tree. Matched by occurrence order within the file (#1, #2, by @michaeldtimpe). Insertions between them are tracked in #3.
- Escapes: `fixtures` joins the default skip directories (#2).
- Every hook and guard firing is one line in `quality/.events.jsonl`; `gate.py --stats [--since 7d]` reports firings, fail rate, fixes after the hook fed a failure back, and what the guard refused while a gate was red. An allowed call records only the tool, never the command; `"events": false` turns the log off. Attach gitignores it.
- The mark: `assets/cleat-mark.svg` and a one-ink `assets/cleat-icon.svg`.
- Attach is local by default; `--ci` adds the workflow, CODEOWNERS and prints the ruleset command. `--git-hooks` writes a pre-push hook. `--refresh` upgrades a vendored copy and migrates the retired complexity shape; `--add` merges the gates a config lacks.
- The Stop hook blocks one stop, not every stop: on `stop_hook_active` the failures are reported and the agent may stop.
- New gates: manifests (a generated project must name every source), inventory (a directory that must not shrink), public-api (a recorded surface must not lose a signature), sarif, doc-citations, reachability, duplication, changed-coverage, escapes.
- Layering and reachability read imports for Python, TypeScript, Rust, Go, Kotlin and Java, or a parser through ast-grep for nine languages.
- Coverage readers take a `path_map` and refuse loudly a report that names no file under the root. xccov notes the Swift-package files it drops.
- Escapes and duplication skip inline `#[cfg(test)]` modules, as complexity did. Reachability takes `exclude` globs. A bare filename in a citation resolves when unique.
- `gate.py --skip-missing-tools` reports a gate whose tool is absent instead of failing it. `--refresh` refuses while a gate or suite is running from the copy.
- Retired: `check-complexity.sh`, `check-complexity-lizard.py`, `lizard_reader.py`, `check-features-map.py`. One `check-complexity.py` reads with lizard or SwiftLint.
- The engine: `ratchet.py` with five outcomes (new, worsened, held, improved, stale), provenance on every baseline, `--strict`. A baselined function that gets worse fails. Failures never print the command that accepts debt.
- `attach.py`: one command to attach, with baselines written so day one is green.
- Conformance fixtures for nine languages; a shared test harness.
- No references to other projects anywhere in the tree.

## 0.0.1

- The original nine checks, extracted from a Swift codebase: complexity (SwiftLint), CRAP, layering, test hygiene, document size, features map, guard suites, mutation, hotspots.

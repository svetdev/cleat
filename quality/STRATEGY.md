# The strategy: what a project measures, and in what order

The README beside this file says how to adopt the checks that exist. This file says why they exist, what belongs beside them as a project matures, and the order that pays off — so the next project starts from a strategy, not a directory listing. Updated 2026-08-23, after a survey of the current tooling.

## The principles the template is built on

1. **Ratchets, not gates.** Every check baselines what exists the day it arrives and fails only new debt. A project is never "too far gone" to adopt a check, and day one is always green. A baseline entry that stops matching anything must be removed — a dead exemption is the same rot the check exists to catch.
2. **One writer per measurement.** The status row, the baselines, the coverage: each is written by exactly one script, and a hand-written entry is a fabricated measurement. Deterministic tools decide; prose persuades.
3. **Every check carries its own test**, driven over a throwaway tree. A gate nobody can trust is worse than no gate: it trains people to ignore red.
4. **Measure what the tests pay for, not what exists.** Complexity alone is a number; complexity the tests do not cover (CRAP) is a risk. The same logic picks every metric here: prefer the compound signal that names an action.
5. **The project's facts live in one file** (`quality.json`); the checks are generic. A check that learns a project's name has forked.

## The adoption ladder

Each rung is useful alone and each makes the next cheaper. Ship a rung per day, not the ladder per quarter.

| Rung | What | Cost | The check |
|---|---|---|---|
| 1 | Config + the language-agnostic pair: document ceilings, test hygiene | hours | `check-doc-size`, `check-test-hygiene` |
| 2 | The runner: ordered preflight, one measured run, the status row, postflight | half a day | the project's own runner (see README: "what stays the project's") |
| 3 | Complexity ratchet | hours | `check-complexity` (SwiftLint today; see roadmap) |
| 4 | Coverage in the run → the CRAP gate | half a day | `check-crap` |
| 5 | A host-less core: the code that needs no app, in a package/crate with its own fast tests | days, proportional | the move tools (`core-pick`/`core-move`-shaped, per stack) |
| 6 | Mutation testing over that core, weekly; harden from survivors | a day, then weekly | `mutate` (Swift) / cargo-mutants (Rust) / Stryker (TS) |

Rung 5 is the one that changes the slope of everything else: tests that run in seconds get written; tests that need a host get skipped.

## Per-stack tool choices

| Concern | Swift | Rust | The template's seam |
|---|---|---|---|
| Complexity | SwiftLint (cyclomatic + length); nesting-weighted evaluated and declined, below | clippy `cognitive_complexity` (restriction-tier) or lizard | `complexity.tool` selects the reader — `"swiftlint"` is the only one wired |
| Per-function coverage | xccov (app) + llvm-cov export (package) | `cargo llvm-cov --json` — same llvm-cov format `check-crap` already reads | `crap.xccov` / `crap.llvm_cov` |
| Mutation | `quality/bin/mutate.py` over the package | cargo-mutants | practice, not config: narrow first, kill every survivor or accept it by name |
| Layering | `check-layering` over source folders | usually unnecessary: a workspace's crate DAG is the check, enforced by the compiler | prefer structure over checks where the build system can refuse |
| Unused deps / dead code | SwiftLint analyzer rules (roadmap) | cargo-machete; `-W unused` is already the compiler's | roadmap |

## Nesting-weighted complexity for Swift: evaluated and declined (2026-08-23)

Cyclomatic complexity counts branches; it does not weight *where* they sit — an `if` inside three nested `for` loops costs the same one point as an `if` at the top of a function, though the first is much harder to hold in your head. A nesting-weighted (cognitive-style) metric adds that weight, and the open question was whether it earns a place beside cyclomatic in the ratchet. Two candidates exist for this stack; both were run over the pilot codebase — an app target and a Swift package, 3,075 functions — and declined:

- **SwiftLint's `nesting` rule** measures a different axis than the one this gate is about. It counts nested *type and function declarations* (`type_level`, `function_level`) — a type inside a type, a closure inside a closure — not nested `if`/`for`/`while`/`switch` bodies. Turned on (`type_level: 1`, `function_level: 2`) it flags 58 declarations today, none of them the deeply-branched functions cyclomatic already watches. It is a real check worth having on its own terms (see the dead-code/analyzer-rules roadmap item), but it is not a cognitive-complexity reader.
- **lizard**'s `-ENS` extension (`max_nested_structures`) does track control-flow nesting depth, and does it generically enough to work for both Swift and Rust — the property that made it attractive as "one tool for both stacks" in the first place. But its Swift reader is broken: `try` is in the same token set as `if`/`while`/`catch` (`lizard_languages/swift.py`'s `_control_flow_keywords`, consumed by the generic nested-structure extension), so `try someCall(args)` is read the same way as `if (someCall(args))` — a paren-delimited control structure — and every `try` increments the nesting pile whether or not anything is actually nested. A synthetic four-line function that is nothing but four flat `try container.decode(...)` calls (no branch, cyclomatic 1) reports NS 5. It is not a corner case: the worst "nesting" in this repo by lizard's own numbers is Codable boilerplate — three `init(from:)` decoders report NS 27, NS 16 and NS 13, each at cyclomatic 1 — flat, mechanical decode bodies with zero branches, not the hardest-to-read code in the tree. `try` appears 650+ times in the app target alone, so the defect isn't rare enough to work around with an exclusion; at the NS>3 threshold, 86 functions are flagged and 53 aren't already caught by cyclomatic>8 — but that "new signal" can't be trusted, since the same `try` artifact is what's inflating most of it.

**Decision: decline.** Neither candidate available to this stack measures nesting-weighted complexity correctly — SwiftLint's rule measures something else, and lizard's implementation of the concept is broken for Swift's `try`. Cyclomatic complexity and CRAP stay the whole complexity signal here. Re-evaluate lizard if its Swift reader is fixed upstream (this is a bug in `lizard_languages/swift.py`, not something this project's `quality.json` can configure around), or judge clippy's `cognitive_complexity` on its own merits when the Rust side of this template is exercised for real — a per-language tool choice, same as the rest of this table.

The wiring still landed: `complexity.tool` (default `"swiftlint"` when a project's `quality.json` predates the key) selects the reader in `check-complexity.py`, and any value that isn't a wired reader fails naming itself rather than silently doing nothing — the seam a second reader will plug into exists without a second reader pretending to work today.

## Structural rules over regex: the first pilot (2026-08-25)

Roadmap item 3 below asked whether ast-grep should replace the pattern layer of the regex-and-strip checks this template's stack accumulates. The pilot is a structural checker in the pilot codebase's tooling — sibling to this template, not part of it, but the sharpest example available: its comment/string stripper had cost three fixture-backed bugs by 2026-08-23, all the same shape — code that lived inside a string literal (an interpolation) was discarded along with the quotes around it, so a reference the compiler would see, the checker didn't.

**What changed.** The checker now parses every file with tree-sitter-swift via `ast-grep-py` (the Python bindings, not a subprocess per file — cheaper, and `sg`'s YAML rule shape is available through the same `kind`/`field` calls without a rule file). Two of its three jobs moved to the tree: `strip()` blanks comment and string-literal-text *nodes* rather than guessing where a literal starts and ends with a regex, so a nested quote, a raw string's `#"` escaping, or an interpolation nested inside a triple-quoted block can't fool it — the grammar parses them for real. `declared()` reads top-level declarations from the nodes that are direct children of the file, rather than approximating "top-level" as "no leading whitespace on the line." The third job — which identifiers and free calls a package test *uses* — stays the existing regexes (`IDENT_RE`, `CALL_RE`), now run over the tree-cleaned text instead of the old regex-cleaned text. That one stayed regex on purpose: `test-move.py` imports `TYPE_DECL_RE`, `LOCAL_DECL_RE`, `IDENT_RE`, `CALL_RE` and `MODS` directly for its own candidate list and its `--duplicates` report, and reclassifying every identifier's syntactic role (argument label vs. value reference vs. declaration) to retire them was a materially larger, riskier change than fixing the two places the three real bugs actually lived.

**Correctness.** Every case in the checker's own suite passes unchanged — the fixtures held as the spec. A new case was added that the old stripper could not express at all, not just handled poorly: a reference named only inside a triple-quoted string's `\(…)` interpolation. The 2026-08-23 fix (`keep_interpolations`) taught the single-quoted-string path to keep interpolation code; the triple-quoted path was never touched, because it ran through a different regex (`re.sub(r'"""(?:.|\n)*?"""', ...)`) that discarded the whole block, interpolations included, before `keep_interpolations` ever ran. Confirmed against the actual old implementation: it drops the reference silently. The tree-based stripper has no separate triple-quoted path to miss — a string literal's content is content, however it's quoted, and the grammar already knows the difference between a quote and the code inside a `\(…)`.

**Timing.** Measured over this checkout — 642 Swift files across the app, its test target, and the package — five runs each, `--app`/`--package` defaults:

| Matcher | Median wall time | vs. regex |
|---|---|---|
| Regex (pre-2026-08-25) | 0.02s | 1x |
| ast-grep, first pass | 1.3s | ~65x |
| ast-grep, after combining `declared()`'s four `find_all` scans into one pass over each file's direct children | 1.0s | ~50x |

The suggested-approach guidance for this item called 2x acceptable and 10x not. ~50x is neither, and profiling says why: 0.7 of the 1.0 seconds is inside `ast_grep_py`'s tree construction itself (parse time, not the Python walk on top of it), across 758 parses (642 files, with the package's test tree parsed twice — once for its declarations, once for its stripped text — a redundancy not yet worth the refactor to remove for ~0.1s). There's no cheap win left: regex substitution over an in-memory string and building a real parse tree are different orders of cost per file, and this check parses every file in the app and the package on every run.

**Decision: adopt anyway, for this check.** Two things point the same way that the raw multiplier doesn't capture. First, the checker runs interactively, by hand, per `core-move.py`/`core-pick.py` invocation during a move — not in a hot per-commit loop or a CI gate on every push, where a sub-second absolute cost compounds. A developer moving a handful of files feels one second once, not fifty times the two hundredths it replaced. Second, the correctness win is structural, not a one-off patch: it closes the whole class the three prior bugs came from, not just the case demonstrated here, and it does the same for `core-pick.py`'s duplicate stripper the next time that file needs the same fix — a regex patch scoped to make the new test pass would have fixed one shape of the bug and left the next one waiting. `quality/`'s own checks (`check-test-hygiene.py`, the layering and CRAP readers) do not carry this pilot's numbers automatically: several read far more than one project's Swift tree per run or run inside a tighter loop (`check-crap.py` as a coverage-run postflight, `check-test-hygiene.py` in every preflight), where a 50x per-check multiplier would land differently. Each earns its own measurement before adopting the same swap — this pilot is evidence for the *pattern* (ast-grep fixes stripper bugs at their root, at a real and now-quantified parse-time cost), not a blanket clearance for every consumer of a regex stripper in this template.

## The roadmap: what earns a place next

Filed as backlog items where this template lives; in the order they pay off.

1. **Hotspots: churn × complexity, as a report.** Complexity alone optimizes files nobody touches; churn alone chases harmless refactors; the product names the refactor that pays (the insight behind CodeScene's model). Both halves already exist here — the lint pass and `git log`. A report, not a gate: it prioritizes work, it does not block a commit.
2. **~~Temporal ratcheting for hygiene ceilings.~~ Done.** A ceiling that only moves when someone improves the count preserves debt indefinitely; a ceiling with a decline schedule makes improvement the default outcome of normal work. `check-test-hygiene.py` reads an opt-in `floor_by` — a habit's map of date to a lower ceiling — and enforces the lowest one whose date has passed; no habit in `quality.json` schedules one yet, that being a decision for whoever owns the habit, not this template.
3. **Structural rules over regex.** The hygiene habits and the cross-reference checks match stripped source with regexes, and every stripper bug is a missed or invented finding. ast-grep (tree-sitter, Swift built in as of 0.45 — no custom grammar needed, via the `ast-grep-py` bindings) replaces the pattern layer; the ratchet layer stays ours. **Piloted 2026-08-25** on the pilot codebase's structural checker — adopted there; see "Structural rules over regex: the first pilot" above for the decision, the ~50x measured parse-time cost, and why it doesn't carry automatically to this template's own checks, which still match stripped source with regexes and are each this item's next candidate.
4. **Dead-code ratchet.** Unreferenced declarations accumulate silently. SwiftLint's analyzer rules read the compile log the suite already writes; Rust's compiler and cargo-machete cover their side.
5. **A flake ledger.** The status record already names every failing test per run; reading it across runs — the same test flipping outcome with no code change — is flake detection from data already written. Quarantine is a human decision the ledger informs, never an automatic pass.

Evaluated and declined, with the reason: platform "code health" scores (opaque, replaces self-tested checks with a vendor's judgement); automatic flaky-test quarantine (a silent pass is the one thing a gate must never do); enabling cognitive complexity as an absolute gate (clippy demoted theirs for good reason — as a *ratchet* it is useful, as a truth it is not); a nesting-weighted complexity ratchet for Swift today (see above — neither candidate reader measures it correctly on this codebase).

## What "done" looks like for a new project

Rungs 1–4 green in the first week; the status file accumulating rows; the first baseline counts falling, not frozen; and the first refactor chosen from the hotspot report rather than from memory. When a second project adopts the template, whatever it had to change lands here — the template forks the day a consumer patches it privately.

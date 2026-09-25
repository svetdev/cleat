# Decisions

Append-only log of choices, findings, disproven assumptions and rules for this repo. Newest at the bottom. An entry records what outlives the change itself — git records what changed.


## [2026-09-23] The guard judges the files a command writes, not text that names a policy file
**Kind:** rule
**What:** `gate.py --guard` refuses a shell command only when a file it writes (a `sed -i` operand, a `cp`/`mv`/`rm`/`tee`/`truncate`/`install` operand, a `>`/`>>` target) resolves to a `quality.json`, the agent settings, or a policy path of a project it lies in. A mention of `quality.json` in a sed script, a comment or a note elsewhere is text.
**Why:** a downstream user's agent was refused a `sed -i` on a memory note outside the repository because the note's text named `quality.json`; a guard that misfires on text teaches the agent to route around it.
**Evidence:** `quality/tests/test-gate.py` "--guard allows" and "--guard refuses" cases; the old pattern matched `sed -i … quality.json …` anywhere on the line.
**Status:** holds

## [2026-09-23] Coverage older than the sources it judges fails the CRAP gate
**Kind:** decision
**What:** when a glob finds a coverage report older than a source file the gate would judge, the gate fails (exit 2) rather than warning. A report named with a flag is read whatever its age; `--estimate` states the age and never refuses.
**Why:** a stale export scores every function as it stood then; a warning beside fifty false findings is not read, and the false findings are what an agent acts on.
**Evidence:** a downstream run read a leftover export and listed 50+ functions over CRAP that were covered; `test-check-crap.py` "refuses the gate: the export is a leftover".
**Status:** holds

## [2026-09-23] Swift 6.4 moved the package coverage export
**Kind:** finding
**What:** `swift test --enable-code-coverage` on Swift 6.4 writes under `.build/out/Products/Debug/codecov`, with `.build/debug` a link to it; a glob over `.build/*/debug/codecov/` keeps matching an export an earlier toolchain left under `.build/<triple>/debug`.
**Why:** the documented example glob was the older layout, so a project that followed it read a leftover without knowing.
**Evidence:** downstream field report, 2026-09-22; `swift test --show-codecov-path` prints the current location. The `check-crap.py` docstring now lists both globs.
**Status:** holds

## [2026-09-23] The Stop hook tightens baselines as the agent goes
**Kind:** decision
**What:** `gate.py --hook` runs every baselined gate with `--tighten`, so an entry the change fixed is dropped and an improved one lowered in the working tree, scoped to the changed files under `--changed`.
**Why:** a fix left the baseline looser than the code, `--strict` failed the preflight on it, and the only remedy was a write the agent had been told not to make. `--tighten` can only lower a baseline, so running it for the agent loosens nothing.
**Evidence:** downstream report: `check-escapes --strict` failed a preflight because two force unwraps were fixed. `test-gate.py` "the hook drops the entry the change fixed".
**Status:** holds

## [2026-09-23] An edited signature is the same function, not new debt; exact values stay
**Kind:** decision
**What:** complexity and CRAP pair an unmatched finding with an unmatched entry that declares the same name in the same file: `changed` when no value went up, `worsened` against the old entry when one did. The rejected alternative was a per-metric tolerance.
**Why:** a tolerance lets a function grow a few lines at a time forever, which is the drift the ratchet exists to stop; the real pain was that touching a declaration line reopened its entry as new.
**Evidence:** `test-ratchet.py` "changed, not new" cases.
**Status:** holds

## [2026-09-23] Recording an API or inventory change stays a person's step
**Kind:** rule
**What:** `check-public-api` and `check-inventory` keep naming `--write-baseline` as the remedy, and the guard keeps refusing it to an agent.
**Why:** a changed public surface or inventory is a deliberate decision, not debt an agent can fix or tighten; being stopped there is the point.
**Evidence:** `quality/bin/check-public-api.py:128`, `quality/bin/check-inventory.py:104`.
**Status:** holds

## [2026-09-25] lizard misreads Swift; cleat masks the source rather than guessing at spans
**Kind:** finding
**What:** lizard 1.24.0 reads a `self.init(`/`super.init(` call as an `init` declaration, and loses brace count at a raw string or regex literal holding a brace and at `#if` branches that each open one. The swallowed functions go unreported, and the enclosing one runs to the end of its type. cleat hands lizard a line-for-line masked copy of each Swift file. It does not flag implausibly large spans, and it does not cross-check against SwiftLint.
**Why:** masking fixes the cause and recovers the swallowed functions. A size heuristic only guesses at the symptom, and a SwiftLint cross-check would make every lizard-configured Swift project depend on a second tool.
**Evidence:** a downstream convenience init read as `init@103-988`, 886 lines; `test-check-complexity.py` Swift fixture, where lizard reads the `self.init` call at line 10 as a function unmasked and every function at its own lines masked.
**Status:** holds

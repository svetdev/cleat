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

## [2026-10-14] cleat's quality evaluation is the baselined debt and its trend, not a score
**Kind:** decision
**What:** `gate.py --summary` reports what each debt baseline records and compares it with the copy git first committed (or the last one before `--since`). It reads the files and runs no gate. There is no combined grade.
**Why:** a count per gate that falls over time is checkable and means the same thing in every project; a weighted score would be the opaque "code health" number `quality/STRATEGY.md` already declines. Reading the baselines keeps the answer instant and tool-free, at the price of being an upper bound until the next `--strict` or `--tighten` run.
**Evidence:** `quality/bin/summary.py`; on this repo, complexity down 8 from 14 at adoption, escapes unchanged at 20.
**Status:** holds

## [2026-10-17] A lizard patch release can move numbers in one language and not another
**Kind:** disproven
**What:** the assumption that lizard 1.24.0 to 1.24.1 changes no measurement held for cleat's own Python (558 functions, identical) but not for a downstream Rust and TypeScript project, whose complexity baseline went from 0 to 27 functions and Rust CRAP from 3 to 30 with no code changed.
**Why:** an upgrade that is a no-op in one repository says nothing about another; the provenance check is what catches it, and each project re-baselines under the new version deliberately.
**Evidence:** cleat `f375668` (version line only); the downstream re-baseline commits of 2026-10-16.
**Status:** holds

## [2026-10-17] Parts are compared with open-source code in their language, as a share, never as a grade
**Kind:** decision
**What:** the summary splits a repository into parts by manifest and places each part's debt per 1k production lines among the parts of a measured open-source sample in the same language (pNN, the share with less). The sample is measured by cleat itself (attach, then the day-one baselines) and the table ships with its provenance.
**Why:** a backend and a frontend in different languages have different normal levels, so one number over both describes neither; and a count per 1k lines means little without knowing what is usual. A share against named, re-measurable repositories stays checkable, which a weighted grade would not.
**Evidence:** `quality/reference.json`, `quality/bin/build-reference.py`, `quality/tests/test-parts.py`.
**Status:** holds

## [2026-10-24] Pre-registration: the measurement study runs on Python first, with fixed procedures
**Kind:** decision
**What:** the study of whether the gates measure anything starts with Python: the 15 Python repositories in `quality/reference-repos.json` for fitting, and 10 held out that the reference does not hold (pallets/jinja, pallets/werkzeug, encode/starlette, python-attrs/attrs, marshmallow-code/marshmallow, Textualize/textual, aio-libs/aiohttp, tqdm/tqdm, pypa/virtualenv, jd/tenacity). A bug fix is a commit whose message matches `fix|bug|defect|regression|crash` and not `typo|doc|readme|lint|style|format|refactor`, touching at least one production `.py` file. SZZ is B-SZZ over `git blame -w -C`, skipping blank and comment-only deleted lines, skipping commits with more than 100 files, and skipping candidate inducing commits whose changed files have an unchanged AST (pure reformats). Fitting uses changes before 2024-01-01; the test window is 2024-01-01 to 2025-06-30, leaving at least 15 months for a bug to be found. The gate signals for a change come from cleat's own extractors on the files before and after the change, with the attach defaults (cyclomatic 8, 60 lines, duplication 6 lines).
**Why:** the disk holds about 15 GB, so full histories for all 111 repositories do not fit; one language done properly is worth more than nine done shallowly, and fixing the procedure before seeing the data is what keeps the result from being fit afterwards.
**Evidence:** `df -h` 15 GiB free on 2026-10-24; the held-out names checked absent from `quality/reference-repos.json`.
**Status:** holds

## [2026-10-24] Prediction: complexity predicts bugs a little beyond size; escapes and duplication do not
**Kind:** finding
**What:** in a change-level logistic model (controls: lines added and deleted, files touched, change entropy, developer experience, all log-scaled; repository as a random intercept, fixed effects as a check), a change the complexity gate would fail has odds of being bug-inducing 1.2 to 1.8 times those of a change of the same size it would pass, with the 95% interval above 1, and adds less than 0.02 AUC on the test window. The escapes and duplication signals add nothing: odds-ratio interval including 1 and less than 0.01 AUC. Size and churn alone reach an AUC of 0.68 to 0.78. The held-out repositories keep the same sign for complexity, with AUC within 0.05 of the test window.
**Why:** prediction, made before the data was mined. Proven wrong if the complexity interval includes 1 (it predicts nothing), its gain is 0.05 AUC or more (much stronger than expected), or either escapes or duplication has an interval excluding 1.
**Evidence:** to come from the study; the procedure is the entry above.
**Status:** superseded by [2026-10-24] Result: complexity goes with bugs but predicts nothing beyond size; copying goes with fewer

## [2026-10-24] Prediction: the ceilings sit inside the benchmark's risk bands; most percentiles are too wide to rank
**Kind:** finding
**What:** the Alves/SIG method (LOC-weighted quantiles of per-function cyclomatic and length over the Python benchmark) puts cyclomatic 8 between its 70% and 90% thresholds and 60 lines between the 80% and 95% length thresholds. Bootstrapping the reference by repository (not by part, since parts of one repository are not independent), at least half of the Python percentiles `--summary` prints for a mid-table value have 95% intervals wider than 0.30, so the summary can tell the top third from the bottom third and nothing finer.
**Why:** prediction. Proven wrong if either ceiling falls outside its band, or if most intervals are narrower than 0.20.
**Evidence:** to come from the study.
**Status:** superseded by [2026-10-24] Result: one ceiling sits at q70 in some languages and q95 in others; every percentile is a band

## [2026-10-24] Prediction: the verdicts hold under renames and reorders, not under reformat, and extraction dodges complexity
**Kind:** finding
**What:** over the Python benchmark, at least 99% of complexity and escapes verdicts survive identifier renames and top-level reordering, while a reformat (`ast.unparse`) changes the 60-line verdict for at least 1% of the functions within 10 lines of it and the duplication verdict for at least 10% of clone pairs. Extracting an over-ceiling function's branches into a single-caller helper clears its complexity verdict in most cases, so the gate cannot tell a dodge from a fix by itself. lizard 1.24.0 and 1.24.1 agree on every Python function. Seeded defects: the complexity gate catches every seeded branch past the ceiling; duplication catches at least 95% of verbatim copies but under half of copies with renamed identifiers; escapes catches every literal pattern but under 70% of a corpus of escapes written to the gate's intent (`except Exception: pass`, `# pyright: ignore`, `cast(Any, …)`). lizard and radon agree on cyclomatic per function with ICC at least 0.9 and kappa at least 0.8 on the over-8 verdict.
**Why:** prediction. Proven wrong by any number on the wrong side of its threshold.
**Evidence:** to come from the study.
**Status:** superseded by [2026-10-24] Result: measurements hold under renames; the ratchet's keys do not

## [2026-10-24] Prediction: agents mostly fix what a gate names, and dodge complexity by mechanical extraction a quarter of the time
**Kind:** finding
**What:** of the gate failures the Stop hook handed back in downstream agent sessions, at least 60% are followed by a real fix, 15 to 30% by a mechanical dodge, under 10% by a relocated escape, and under 5% by an attempt on the guard. A complexity response counts as a dodge when the function and its new helpers together carry at least the original cyclomatic count and every new helper has one caller; an escape is relocated when the same pattern reappears at a site the baseline does not key; a guard attempt is a tool call the guard refused or would have refused. The categories are decided by those rules, not by reading. In a paired run of the same real tasks with and without cleat attached, the tests pass at rates within 10 points of each other, the cleat arm has a lower maximum cyclomatic and more functions, and a blind reviewer's preference does not differ from even.
**Why:** prediction. Proven wrong if real fixes are under 50% or dodges over 40%. The blind review needs engineers outside the project and cannot be done by an agent; until it is, that part stays open.
**Evidence:** to come from the study.
**Status:** superseded by [2026-10-24] Result: agents fix what most gates name; complexity is dodged more often than fixed

## [2026-10-24] Result: complexity goes with bugs but predicts nothing beyond size; copying goes with fewer
**Kind:** finding
**What:** over 63,083 changes in 25 Python repositories, size and churn alone rank bug-inducing changes at AUC 0.766 on the 2024-01 to 2025-06 window. A change the complexity gate would fail has 1.30 times the odds of being bug-inducing (95% 1.21 to 1.40; 1.33 in the held-out repositories), yet adds −0.0005 AUC: an association, not a prediction. Escapes: 1.07 (0.98 to 1.18), nothing. Duplication: 0.74 (0.65 to 0.84) — a change that copies a block is less likely to induce a bug, the opposite of the gate's premise. No signal, alone or together, adds 0.01 AUC on either the test window or the held-out repositories (held-out base AUC 0.776).
**Why:** the complexity and escapes predictions held and the duplication one was falsified. B-SZZ blames the lines a fix deletes, and a long function collects more of a fix's deletions, so the complexity association may partly be the labeller's; the absent AUC gain does not depend on that.
**Evidence:** fixed-effects logit with cluster-robust errors by repository; the random-intercept fit (variational Bayes) gives 1.30, 1.07 and 0.74 too. Train 37,303 changes (28% bug-inducing), test 2,618, held-out test 3,852. The ratchet's pairing of namesakes was first mis-implemented in the miner (it paired an over-ceiling method with another class's namesake) and the rows were re-mined before the models ran.
**Status:** holds

## [2026-10-24] Result: one ceiling sits at q70 in some languages and q95 in others; every percentile is a band
**Kind:** finding
**What:** by the Alves/SIG method over the re-measured 111 repositories, cyclomatic 8 is the 70% LOC-weighted threshold in Python and Go, between 70% and 80% in TypeScript, 80% in Swift, between 80% and 90% in Java, 90% in Kotlin and 95% in Ruby; 60 lines sits between Python's 70% (50) and 80% (68) thresholds, outside the 80–95% band predicted. Bootstrapped by repository, a mid-table Python part's percentile has a 95% interval about 0.44 wide on every one of the seven measures (Rust escapes at 0.24 is the narrowest anywhere, Java duplication at 0.78 the widest); even p10 and p90 carry about 0.28.
**Why:** the cyclomatic prediction held at its edge, the length one was falsified, and the width prediction held. A "p73" in the summary means somewhere from the middle to the top third, and the same ceiling refuses three times as much of a Ruby codebase's code as of a Go one's.
**Evidence:** 2026-10-24 re-measurement at each repository's HEAD reproduces the shipped table's part counts (Python 18 parts, 18 repositories) and click's values exactly; 2,000 repository resamples.
**Status:** holds

## [2026-10-24] Result: measurements hold under renames; the ratchet's keys do not
**Kind:** finding
**What:** over 16,972 Python functions in the 25 repositories, every complexity and escapes measurement survives renames and reorders, and lizard 1.24.0 and 1.24.1 agree on every Python and Swift function. The ratchet does not: renaming a private function makes 17% of baselined over-ceiling functions read as new (22 of 25 repositories fail), renaming a local on an escape's line makes 28% of baselined sites new, and black makes 22% of baselined functions read as worsened by length. The duplication changed-lines check fails 25 of 25 repositories after a rename and 22 of 25 after a reorder, since any edit inside an existing clone fails it. Extracting branch bodies into single-caller helpers clears a quarter of over-ceiling functions (44% when repeated), never lowering total cyclomatic. Seeded defects: complexity 71 of 72 (lizard does not count Ruby `unless`), verbatim copies 45 of 45, copies with renamed identifiers 0 of 45, literal escapes 70 of 70, escapes written to the gate's intent 19 of 120. lizard agrees with radon on Python (ICC 0.996, kappa 0.97 on over-8), less with ESLint on TypeScript (0.73) and SwiftLint on Swift (0.53).
**Why:** a ratchet that treats a rename as new debt fails an agent for a refactor and teaches it the gate is noise; the prediction held as measurement and was falsified as the gate behaves. Extraction clearing most functions was falsified: if/elif ladders keep their complexity in the conditions.
**Evidence:** the gate reading was checked against the real `check-*.py` gates on 4 repositories × 3 transforms, all 12 failure counts equal; the transformed click and tenacity suites pass under rename, reorder and reformat. The seeded corpus was hashed before any gate ran on it.
**Status:** holds

## [2026-10-24] Result: agents fix what most gates name; complexity is dodged more often than fixed
**Kind:** finding
**What:** in 329 gate-failure events across 3 downstream projects (35 sessions), agents fixed 91–100% of test-hygiene, duplication, escapes and document failures, and relocated an escape twice in 23. Of the 95 complexity failures whose code could be reconstructed, 56% ended in a mechanical dodge (single-caller helpers, total cyclomatic not lower) and 37% in a real fix. Overall, real fixes are 60% with undetermined events excluded and 49% with them counted against the prediction, which falsifies it on that reading. Guard refusals are 11%, but 36 of 37 trace to one commit refused by an older downstream copy of cleat for a message naming `--write-baseline`; the current guard allows such a commit.
**Why:** the gates that name a concrete fix get it; the complexity gate names a number, and the cheapest way to move a number is to move code. The paired with/without-cleat runs and the blind review were not run: the first is a deliberate spend and the second needs engineers outside the project.
**Evidence:** transcripts from 2026-09-19 on (earlier ones are pruned: the projects' event logs hold 63 blocking hook failures, the transcripts 32); one project carries 81% of events and Rust 66%; 62 complexity events are undetermined.
**Status:** holds

## [2026-10-24] Go layering reads every allowed import as a violation
**Kind:** finding
**What:** the Go resolver returns an import's package directory, and `layer_of` gives a one-part path the layer `App`, so an allowed downward import from a Go layer fails. `test-layering-imports.py` tests only the forbidden direction.
**Why:** a gate that fails clean code is trained out of being read; found by the seeded-defect run, not yet fixed.
**Evidence:** `quality/bin/extractors/references.py` `_resolve_go`, `quality/bin/check-layering.py:133`; 3 false violations on a clean Go template.
**Status:** holds

## [2026-10-25] Pre-registration: the paired agent runs, task rule and scoring fixed before any task is drawn
**Kind:** decision
**What:** 10 real tasks, each run once by an agent without cleat and once with `attach.py` applied, same prompt and model (`claude -p`, claude-opus-5-5, project settings only, no web tools, $5 cap), and the same permissions in both: denied unless allowed, edits only inside the task's tree, Bash only for the project's Python and read-only inspection, Bash in the OS sandbox with no network and no read of the clones or the drawn tasks. A task is a non-merge commit from 2024-01-01 to 2025-06-30 in one of the 25 mined Python repositories that changes 1 to 3 production files with 10 to 150 added production lines and changes a test file, and whose changed tests include at least one that fails on the parent and passes at the commit (fail-to-pass), with every other test in those files passing at the commit. Candidates are drawn in a seeded order (20261025), at most 2 per repository, until 10 validate. The agent sees the linked pull request's title and description (its issue appended when it names one; diff blocks removed), or the commit message when there is no pull request, and a tree with no history. Scored: resolved (all fail-to-pass and pass-to-pass tests pass after the commit's test files are copied over the agent's tree), and on the agent's production diff the complexity, escapes and duplication verdicts as mined in the JIT study, the maximum cyclomatic among changed functions, functions added, lines changed, turns and cost. "Later rework" is dropped: a single run has no later.
**Why:** a design fixed after seeing tasks can be steered toward a result; ten pairs is a pilot, and the predictions below are about counts, not significance.
**Evidence:** predictions: the arms resolve within 1 task of each other; the run without cleat ends with a complexity, escapes or duplication verdict failing in at least 3 tasks and the cleat arm in at most 1; the cleat arm adds more functions in at least 5 of the pairs that differ and costs 20 to 60% more per task. Proven wrong by any count outside those.
**Status:** superseded by [2026-10-25] Result: with cleat attached, no run ended with debt the gates name; without it, four did

## [2026-10-25] Result: with cleat attached, no run ended with debt the gates name; without it, four did
**Kind:** finding
**What:** over 10 real tasks from 8 Python repositories, run once without cleat and once with it, the agent resolved 8 without and 9 with (the extra one a textual layout fix; a celery task neither resolved). Without cleat, 3 runs ended with a complexity, escapes or duplication verdict failing as the JIT study mines them, and 4 by cleat's real gates (two worsened functions with copied blocks, one broad `except Exception:` silenced with `# noqa: BLE001`, one copied block); with cleat, 0 by either. On the two tasks where the plain run worsened a function, the cleat run left the changed functions at cyclomatic 8 and 16 against 15 and 16. Cleat cost a median 35% more per task (33% in total) and more turns. Functions added differed in only 4 pairs (3 with more under cleat), so the prediction of at least 5 is falsified by too few differing pairs; the other four predictions held.
**Why:** this is the measured counterpart to the JIT result: the gates' signals do not predict bugs beyond size, but an agent held by them does leave less of the debt they name, for about a third more cost, without resolving fewer tasks. Ten pairs is a pilot: one task per arm is the whole difference in resolution.
**Evidence:** `study/paired/` (draw, run, score, summarize). Both arms ran `claude -p` with claude-opus-5-5, permissions denied by default, a fixed allowlist and sandboxed Bash; a first pilot ran with the sandbox auto-approving every Bash command, which made the allowlist moot, and was discarded and re-run before the other nine. 1,057 candidates were drawn from; most exclusions were packages that do not build on Python 3.14 (aiohttp, pydantic-core, pytest), so the sample leans to pure-Python repositories.
**Status:** holds

## [2026-10-25] Pre-registration: the paired runs at 30 tasks, twice each, against the cleat that counts helpers
**Kind:** decision
**What:** the 2026-10-25 design again, scaled: 30 tasks by the same rule and seeded order, at most 4 per repository (at 2, too few repositories build on Python 3.14 to reach 30), each run twice without cleat and twice with it — 120 runs — under the same model, prompt and permissions. The cleat arm is the working tree after the rename-proof keys, the base-relative duplication judgment and `"helpers": "inline"`, which `attach.py` now writes. Scored as before; added: how often the two runs of one arm on one task disagree on resolution, the noise any difference between arms must exceed.
**Why:** ten pairs could not tell a cost in resolved tasks from noise; two runs per arm measure the noise directly instead of assuming it.
**Evidence:** predictions: per run, the arms resolve within 3 tasks of 30 of each other on average over the repeats, and the two runs of one arm disagree on at least 2 tasks; without cleat at least 12 of 60 runs end with a mined verdict failing, with cleat at most 3; cleat costs 20 to 60% more per task (median). Proven wrong by any count outside those.
**Status:** holds

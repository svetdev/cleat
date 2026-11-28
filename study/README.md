# The studies

This directory holds every study run on cleat: the scripts, how to run them, and what they found. The results themselves, with their numbers and their predictions, are dated entries in `DECISIONS.md`; this page is the index to them and the way to repeat any of them on another repository.

Each `.py` file's docstring is its manual: what it reads, what it writes, and the rule it applies.

## How a study is run here

- **Predict first.** Before a study that spends tokens, the predictions go into `DECISIONS.md` as a `Prediction:` entry. The result is a separate entry that says which held. A change to the rule after the data is in is recorded as a `Deviation:`.
- **Hold everything but one thing fixed.** The arms get the same task text, model, budget, flags and sandbox. Only what is being tested differs.
- **Judge by something the agent cannot see.** Hidden tests, a fresh attach of cleat on the result, a reviewer who is not told which arm wrote which diff.
- **Run each cell at least twice.** Two runs of the same arm on the same task disagreed on 2 tasks in 30. A gap smaller than that is noise.
- **Read a sample by eye.** Every count here was checked against the lines behind it. Two were wrong on the first pass: an event tally inflated 2.4 times by a copied log, and a tree baseline of zero from reading a bare clone's empty work tree.

## Where things go, and what leaves the machine

- A study's work dir sits outside the cleat checkout, with no `CLAUDE.md` or `AGENTS.md` in any directory above it. An agent loads every such file it finds.
- Clones may be bare. The scripts read them with `git show` and `git ls-tree`.
- Every agent run is `claude -p` on the account logged in on the machine, in a sandbox with no network. The code the agent reads goes to the model and nowhere else. Check that this is allowed for the repository before the first run.
- The scripts that start no agent send nothing anywhere.
- `replay/share.py` writes a card with no repository name, path, commit, date, command or code. Nothing is shared unless that one file is put in a pull request by hand.
- Before a paid batch, write down its size: tasks × arms × repeats, at $0.25 to $0.80 a run in the studies so far.

## What was asked, and what came back

| question | answer | scripts | `DECISIONS.md` |
|---|---|---|---|
| Do the gates' verdicts predict a bug-inducing change? | Complexity goes with bugs (odds 1.30) and predicts nothing beyond size. Escapes: nothing. Copying goes with fewer bugs. | `jit/` | 2026-10-24 |
| Where do the ceilings sit against other code? | Cyclomatic 8 is the 70% threshold in Python and the 95% in Ruby. A percentile is a wide band. | `reference/` | 2026-10-24 |
| Do the measurements survive a rename, a reorder, a reformat? | The measurements do. The ratchet's baseline keys did not. | `reliability/` | 2026-10-24 |
| What does an agent do when a gate fails? | It fixes most gates' failures. Complexity is dodged with single-use helpers about as often as it is fixed. | `agents/` | 2026-10-24 |
| Does an agent with cleat leave less of the debt the gates name? | Yes: 5 of 60 runs ended with a gate failing against 43 of 60. Tasks resolved did not move. | `paired/` | 2026-10-25, 11-02, 11-03 |
| Do words alone, or more effort, do the same? | No. A paragraph of instructions and an agent asked to try harder both left the debt where it was. | `paired/run.py` arms | 2026-11-10, 11-17 |
| Does a reviewer prefer the gated diff? | No. Neither arm in Python; the ungated one in Rust, for correctness. | `paired/review.py` | 2026-11-02, 11-17, 11-18 |
| Over a long history, is gated code easier to change? | Gated trees end with fewer complex functions. Later changes cost the same on both, and the arms do not move apart along a chain. | `replay/`, `replay/drift.py` | 2026-11-04, 11-05, 11-18, 11-23 |
| Do the gated trees' tests catch more planted bugs? | About 12 points more, late in a run only. | `replay/mutate.py` | 2026-11-05, 11-07 |
| Does a change cost more where the code is more complex? | Barely, and not once the size of the change is held fixed. | `replay/dose.py`, `target.py`, `pairs.py` | 2026-11-18, 11-19 |
| How many commits that maintainers merged would the gates refuse? | 44% with the first defaults, 22% with today's. | `replay/history.py`, `gates/refusals.py` | 2026-11-19 |
| Do bug fixes land on lines that carry an escape? | No more than other commits do. | `gates/escapes_fixes.py` | 2026-11-19 |
| Which gates ever fire, and does an agent try to change the policy? | Hygiene and exclusions never fired. Tampering is rare. | `gates/events.py` | 2026-11-19 |
| Do agents leave dead code? | Almost none. | `gates/dead.py` | 2026-11-19 |
| Does low coverage of the changed lines mark a wrong solution? | No. Read with the agent's own tests, a low-coverage run was wrong 21% of the time against 19%, and one touching a function with CRAP over 30, 20% against 19%. The earlier 61% against 19% was hidden tests that could not load against a wrong solution, counted as low coverage. | `gates/own_coverage.py`, `gates/coverage.py` | 2026-11-23 |
| After maintainers split a function that was over the ceiling, is it fixed less often? | Not measurably. Over 145 such functions in 37 repositories, fixes per 1,000 lines fall 19% in the year after; on 758 functions over the ceiling left alone they fall 29%. Split functions were fixed more before and stay so. Too few to rule out a third either way. | `gates/refactors.py` | 2026-11-23 |
| Does enforcing that coverage make agents more correct? | No: 6 of 24 resolved with it and without it. | `paired/run.py`, `STUDY_COVERAGE` | 2026-11-19 |
| How often does an agent break a rule the project keeps for itself? | One run in ten: a public signature, a passing test, the project's lint or format. The same with and without cleat. | `gates/rules.py` | 2026-11-22 |
| Do agents leave files nothing references? | A few scratch files over 80 to 150 changes. | `gates/unreached.py` | 2026-11-22 |
| Are the commits the gates refuse the ones later fixed? | Yes: 26% against 17% in 259 pairs matched for size (p 0.006), mostly complexity and duplication. An association, not the gate preventing the fix. | `replay/history.py`, `gates/later_fixes.py` | 2026-11-22 |
| Are commits that credit an AI assistant refused more? | No, once size is held: they are larger. | `gates/eras.py` | 2026-11-22 |
| Does a weaker model need the gates more, or stall on them? | Neither. | `paired/run.py`, `STUDY_MODEL` | 2026-11-22 |
| Do the gates work on Swift and Kotlin? | After six fixes to attach and duplication, 15% of merged commits refused, as elsewhere. | `replay/history.py` | 2026-11-22 |
| Does a later fix land on the code the gate named? | No more than on the rest of the commit, outside one repository. | `gates/fix_sites.py` | 2026-11-22 |
| Does an agent with cleat avoid a bug the merged commit carried? | No: on 11 such commits the bug came back or not by the task, identically in both arms. | `paired/bugdraw.py`, `paired/bugscore.py` | 2026-11-22 |
| Does the project's own lint and format, as a gate, stop agents breaking them? | Yes: 0 of 48 runs against 5 of 48, at the same cost and tasks resolved. | `paired/run.py`, `STUDY_RULES`, `paired/project_rules.py` | 2026-11-22 |
| Do changed-line coverage or CRAP as gates make a top model more correct, or its tests stronger? | No measurable change, because they rarely fire. Over 31 proven tasks twice each: CRAP at 30 refused nothing in 62 runs, coverage refused in 4, CRAP at 8 in 10, of which 8 named a function already 94% to 100% covered. Resolved 46, 48, 48, 47 of 62 at the same cost; planted bugs caught 76%, 70%, 68%, 74% on about 115 per arm, too few to tell apart. | `paired/proof.py`, `paired/sandboxed.py`, `paired/run.py`, `STUDY_CRAP`, `paired/mutants.py` | 2026-11-23 |
| Do the gates work on apps, as against libraries? | They refuse 26% of merged commits on eight iOS and Android apps, a third of the complexity refusals for a long body with few branches. | `replay/history.py` | 2026-11-22 |
| Do a gated tree's earlier tests stop a faulty later change more often? | A little, not clearly. On two TypeScript replays, 43.6% of 321 one-operator faults in real later changes against 38.3% of 347; over 19 later changes, higher in 9, lower in 5, level in 5. | `replay/later.py` | 2026-11-23 |
| On code with no tests, do coverage or strict CRAP fire, and do the agent's tests get stronger? | They fire, and the tests are not measurably stronger. With the test files removed, 20 tasks: the agent wrote a test in nearly every run with no gate asking. Coverage refused in 2 of 40 runs. CRAP at 8, over eight runs a task, refused in 42 of 160 and was answered each time; planted bugs caught 84.1% against 79.7%, per task +2.3 points (interval -3.1 to +8.5); resolved 112 against 117 of 160; cost 1.06 times. | `paired/run.py`, `STUDY_STRIP_TESTS`, `paired/mutants.py` | 2026-11-24 |
| Does the public-api gate stop agents dropping a public signature? | At module level there was nothing to stop: 0 of 70 runs with or without it. With methods read (the reader was changed after the first batch), one task in 35 loses public methods upstream kept in every ungated run, and gated runs keep them. Same cost and tasks resolved. Where the task itself is a break the agent keeps the old name or stops red for a person; none rewrote the baseline. | `paired/run.py`, `STUDY_API`, `gates/rules.py` | 2026-11-24 |
| When the complexity gate refuses, does the agent simplify or pay the metric? | In 48 of 89 refused runs it re-expressed a decision in a form the count passes over (a dict lookup, `filter`, `\|=`); in 24 it removed or merged decisions; in 12 it shared logic; a single-use helper in 1. No behaviour dropped. Four functions make up 82 of the runs. | `gates/metric_paid.py` | 2026-11-24 |
| With today's gates, how much debt does an ungated agent leave, and what does the gate cost? | A run ends with debt a default gate names in 34 of 70 ungated runs and 0 of 70 gated, at 1.2 times the cost. Tasks resolved, planted bugs caught and lint breaks are the same in both. | `paired/run.py`, `paired/mutants.py`, `gates/rules.py` | 2026-11-24 |
| Does the lint and format gate work on tasks not picked for it? | Yes: new lint in 0 of 58 runs with the gate, 9 of 58 with cleat as attached and 7 of 58 without, at 1.1 times the cost and the same tasks resolved. One run left a file unformatted through a second stop the hook let pass. | `paired/run.py`, `STUDY_RULES`, `gates/rules.py` | 2026-11-24 |
| Which part of cleat does the work: the words, the gates or the Stop hook? | The gates. Debt a gate names in 34 of 70 runs without cleat, 20 with a paragraph on complexity alone, 0 with the gates and no hook, 0 with cleat whole. The paragraph cut complexity debt from 19 runs to 5 and left escapes and duplication, which it does not name, where they were. Every no-hook run ran the gates itself. | `paired/run.py`, `STUDY_WORDS_ONLY`, `STUDY_NO_HOOKS` | 2026-11-24 |
| Do coverage and CRAP behave in TypeScript as in Python, on code with its tests removed? | Mostly. The agent wrote a test in 40 of 40 runs under today's gates alone. Coverage refused in 12 of 40 runs and CRAP in 13 of 40; tasks resolved 27, 29 and 28 of 40; cost 1.11 and 1.18 times. But 10 of the 12 coverage refusals named only a checked-in generated copy of the source that no test runs. With the tests left in place (72 runs): refused in 5 and 6 of 24, resolved 20, 19 and 20 of 24, cost level. Bugs caught not measured | `paired/typescript/tsrun.py`, `STUDY_COVERAGE`, `STUDY_CRAP`, `STUDY_STRIP_TESTS` | 2026-11-24 |
| Do the gates stop debt growing on a codebase that already has it? | Yes. From 105 functions over cyclomatic 8, three gated chains ended at 101 to 102 and three ungated at 111 to 113 over 100 real changes; upstream ended at 114. Cost 1.2 times. Resolved 42 against 53 of 165, confounded by rollbacks; later-change probes not run | `replay/chain.py` with a `replay.json` adapter | 2026-11-25 |

Not yet studied: CRAP or coverage with a model that leaves its change untested; the other project-rule gates as gates an agent runs under (conventions, layering, inventory, SARIF, doc citations, guard suites); any codebase with years of debt; an agent working in an app; sessions with a person in them.

## On your own repository

In order of cost. The first five start no agent. `examples.md` has one public repository per language to try the first and third on, with what they printed. `try-it.md` is the first and third as one prompt for a coding agent, ending in a card that can be shared.

**1. What the gates would have refused.** For the last 60 commits that change a source file, cleat is attached at the parent and the gates run on the commit as the Stop hook runs them.

```
python3 study/replay/history.py ~/study/clones/acme__app ~/study/history 60 6
python3 study/gates/refusals.py ~/study/history
```

A commit a person merged and a gate refuses is a false refusal or debt someone chose to take. `refusals.py` prints the rate, the split by gate and by kind, and a table of growth allowances. Then read twenty of the refused commits' `said` field in the JSON by eye: on the first Swift and Kotlin repositories that reading found four defects in cleat. Needs lizard on the PATH.

With the stretch started further back (`history.py … 60 6 HEAD~400`), `gates/later_fixes.py <history dir> <clones dir>` says whether the refused commits are the ones a fix later landed on. Where commits credit an assistant in their message, `gates/eras.py` compares those with the commits beside them.

**2. Whether escapes sit where the bugs were.**

```
python3 study/gates/escapes_fixes.py ~/study/escapes.json ~/study/clones/acme__ios:swift ~/study/clones/acme__android:kotlin
```

The patterns per language are `quality/bin/check-escapes.py --list-languages`. A fix is a commit whose subject says fix, bug, regression, crash or panic; a repository that names fixes another way needs `FIX` changed.

**3. Where the code stands.** After `attach.py --into <tree>`, `quality/bin/gate.py --summary` prints the recorded debt per 1,000 lines against the reference sample.

**4. Whether agents broke the project's own rules.** Where agent runs are on disk, `gates/rules.py` counts the ones that dropped a public signature, broke a passing test, or added a lint or format violation.

**5. What agents already did, where cleat is attached.** `gates/events.py <project dir>` reads cleat's event log: every Stop-hook run, which gates failed, every call the guard refused. `agents/run.sh` goes further and classifies what the agent did after each failure from the session transcripts, as a fix, a dodge or a relocation; it needs the private `anon.json` its header describes.

**6. The replay.** An agent rebuilds a stretch of the repository's real commits with and without cleat. `replay/README.md` is the run order. It needs a `replay.json` adapter: an install command, a test command that runs the files it is given and writes JUnit XML, and the commands the agent may run. A first look is 20 changes and $40 to $60.

For a mobile repository the adapter is the work, and nobody has written one yet:

- **The suite must run offline** in a sandbox, from the command line, in minutes. A Gradle build needs `--offline` and a warm cache named in `tool_dirs`. An Xcode build needs its packages resolved beforehand.
- **Unit tests only.** A suite that needs a simulator or an emulator will not start in the sandbox. `swift test` on a package, or JVM unit tests under Gradle, are the ones to try first.
- **JUnit XML in one file.** `swift test --xunit-output {out}` writes it. Gradle writes one file per class under `build/test-results`, so the test command has to merge them into `{out}`.
- **Run `replay/doctor.py` until it says `ready for a replay`** before anything that costs tokens, then the oracle chain alone.
- **Put a time limit in every command.** A suite that hangs holds the run for good; one did for five hours.

**7. Paired tasks.** `paired/draw.py` draws single commits with a failing-then-passing test and `paired/run.py` runs each with and without cleat. It exists for Python, TypeScript (`paired/typescript/`) and Rust (`paired/rust/`) only. For another language the replay's scored changes are the same thing through the adapter.

## Arms

`paired/run.py` builds an arm from environment variables, so a new question is usually a new arm, not new code. `STUDY_ARMS=plain,cleat,x` names them; an arm other than `plain` attaches cleat.

| variable | the arm it makes |
|---|---|
| `CLEAT_<ARM>=<checkout>` | attaches another version of cleat |
| `STUDY_NO_HOOKS=x` | what attach wrote, with no hooks: told and not enforced |
| `STUDY_WORDS_ONLY=x` | a paragraph of instructions and nothing else |
| `STUDY_EFFORT=x` | no cleat, asked to reread, test and rework before stopping |
| `STUDY_CEILINGS=x=12` | another cyclomatic ceiling |
| `STUDY_DROP_GATES=x=complexity+duplication` | attached with those gates taken out |
| `STUDY_COVERAGE=x` | changed-line coverage enforced at every stop |
| `STUDY_CRAP=x` or `x=8,y=30` | CRAP at threshold 8, or the one given per arm, on the changed files at every stop, after the whole suite runs under coverage |
| `STUDY_RULES=x` | the project's ruff lint and format as a `commands` gate |
| `STUDY_MODEL=<model id>` | another agent model, for every arm |

`paired/arms.py <work dir> <arm> …` reads any of them: tasks resolved, runs ending with a gate failing, median cost and turns. `replay/target.py` and `replay/pairs.py` take `STUDY_TREES`, `STUDY_ATTACH`, `STUDY_LABEL` and `STUDY_MODEL` the same way.

## What the studies add up to

Cleat's enforcement works: an agent satisfies whatever the Stop hook checks, runs the gates itself, and almost never touches the policy. What the default gates check is the shape of the code, and no study found that a better shape made the result more correct, easier to change or preferred by a reviewer. The one gate that removed a harm outright was a project's own lint and format, added as a `commands` entry. Every study used a strong model on well-kept open-source repositories in Python, TypeScript and Rust. A different codebase, language or agent is exactly what has not been measured.

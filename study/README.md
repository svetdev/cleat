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
| Over a long history, is gated code easier to change? | Gated trees end with fewer complex functions. Later changes cost the same on both. | `replay/` | 2026-11-04, 11-05, 11-18 |
| Do the gated trees' tests catch more planted bugs? | About 12 points more, late in a run only. | `replay/mutate.py` | 2026-11-05, 11-07 |
| Does a change cost more where the code is more complex? | Barely, and not once the size of the change is held fixed. | `replay/dose.py`, `target.py`, `pairs.py` | 2026-11-18, 11-19 |
| How many commits that maintainers merged would the gates refuse? | 44% with the first defaults, 22% with today's. | `replay/history.py`, `gates/refusals.py` | 2026-11-19 |
| Do bug fixes land on lines that carry an escape? | No more than other commits do. | `gates/escapes_fixes.py` | 2026-11-19 |
| Which gates ever fire, and does an agent try to change the policy? | Hygiene and exclusions never fired. Tampering is rare. | `gates/events.py` | 2026-11-19 |
| Do agents leave dead code? | Almost none. | `gates/dead.py` | 2026-11-19 |
| Does low coverage of the changed lines mark a wrong solution? | Yes: wrong 61% of the time against 19%. | `gates/coverage.py` | 2026-11-19 |
| Does enforcing that coverage make agents more correct? | No: 6 of 24 resolved with it and without it. | `paired/run.py`, `STUDY_COVERAGE` | 2026-11-19 |

Not yet studied: reachability, and the gates that hold a project's own rules (conventions, layering, public API, inventory, manifests, SARIF, doc citations, guard suites).

## On your own repository

In order of cost. The first four start no agent.

**1. What the gates would have refused.** For the last 60 commits that change a source file, cleat is attached at the parent and the gates run on the commit as the Stop hook runs them.

```
python3 study/replay/history.py ~/study/clones/acme__app ~/study/history 60 6
python3 study/gates/refusals.py ~/study/history
```

A commit a person merged and a gate refuses is a false refusal or debt someone chose to take. `refusals.py` prints the rate, the split by gate and by kind, and a table of growth allowances. Then read twenty of the refused commits' `said` field in the JSON by eye. Needs lizard on the PATH. Swift, Kotlin, Java, Go and Ruby are read as Python, TypeScript and Rust are, but none of them has been through this study yet.

**2. Whether escapes sit where the bugs were.**

```
python3 study/gates/escapes_fixes.py ~/study/escapes.json ~/study/clones/acme__ios:swift ~/study/clones/acme__android:kotlin
```

The patterns per language are `quality/bin/check-escapes.py --list-languages`. A fix is a commit whose subject says fix, bug, regression, crash or panic; a repository that names fixes another way needs `FIX` changed.

**3. Where the code stands.** After `attach.py --into <tree>`, `quality/bin/gate.py --summary` prints the recorded debt per 1,000 lines against the reference sample.

**4. What agents already did, where cleat is attached.** `gates/events.py <project dir>` reads cleat's event log: every Stop-hook run, which gates failed, every call the guard refused. `agents/run.sh` goes further and classifies what the agent did after each failure from the session transcripts, as a fix, a dodge or a relocation; it needs the private `anon.json` its header describes.

**5. The replay.** An agent rebuilds a stretch of the repository's real commits with and without cleat. `replay/README.md` is the run order. It needs a `replay.json` adapter: an install command, a test command that runs the files it is given and writes JUnit XML, and the commands the agent may run. A first look is 20 changes and $40 to $60.

For a mobile repository the adapter is the work, and nobody has written one yet:

- **The suite must run offline** in a sandbox, from the command line, in minutes. A Gradle build needs `--offline` and a warm cache named in `tool_dirs`. An Xcode build needs its packages resolved beforehand.
- **Unit tests only.** A suite that needs a simulator or an emulator will not start in the sandbox. `swift test` on a package, or JVM unit tests under Gradle, are the ones to try first.
- **JUnit XML in one file.** `swift test --xunit-output {out}` writes it. Gradle writes one file per class under `build/test-results`, so the test command has to merge them into `{out}`.
- **Run `replay/doctor.py` until it says `ready for a replay`** before anything that costs tokens, then the oracle chain alone.
- **Put a time limit in every command.** A suite that hangs holds the run for good; one did for five hours.

**6. Paired tasks.** `paired/draw.py` draws single commits with a failing-then-passing test and `paired/run.py` runs each with and without cleat. It exists for Python, TypeScript (`paired/typescript/`) and Rust (`paired/rust/`) only. For another language the replay's scored changes are the same thing through the adapter.

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
| `STUDY_MODEL=<model id>` | another agent model, for every arm |

`paired/arms.py <work dir> <arm> …` reads any of them: tasks resolved, runs ending with a gate failing, median cost and turns. `replay/target.py` and `replay/pairs.py` take `STUDY_TREES`, `STUDY_ATTACH`, `STUDY_LABEL` and `STUDY_MODEL` the same way.

## What the studies add up to

Cleat's enforcement works: an agent satisfies whatever the Stop hook checks, runs the gates itself, and almost never touches the policy. What the default gates check is the shape of the code, and no study found that a better shape made the result more correct, easier to change or preferred by a reviewer. Every study used a strong model on well-kept open-source repositories in Python, TypeScript and Rust. A different codebase, language or agent is exactly what has not been measured.

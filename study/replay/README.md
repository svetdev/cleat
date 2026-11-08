# The replay, on your own repository

The replay takes a stretch of real commits from a repository and has an AI agent rebuild them one at a time, twice: once with cleat's gates attached (`cleat`) and once without (`plain`). A third chain, the oracle, lays the real commits on with no agent, to prove the harness works on your code. Then it measures what each arm left: complexity, how many changes resolve, how hard the code is for a fresh agent to change, and how many planted bugs its tests catch.

This page is the run order. The design and the study's own results are in `DECISIONS.md`.

## What you need

- **A Mac.** The harness clones trees with APFS and runs the agent in Claude Code's macOS sandbox. Linux copies instead of cloning, but no one has run a whole replay there yet.
- **Python 3.9+, git, and lizard** (`pip install lizard`) for the complexity counts.
- **Claude Code, logged in.** Every change is one `claude -p` run on `claude-opus-5-5`, capped at $5.
- **The GitHub CLI, logged in, if the repository is on GitHub.** Each commit's task text is its pull request and linked issue, which works for private repositories you can read. Without `gh`, the agent gets the commit message.
- **A test suite that runs offline and passes** at the commit where the stretch starts. The agent has no network.
- **A clean checkout of cleat.** The `cleat` arm attaches cleat from the checkout you run the scripts from and records its commit, so uncommitted changes there would be measured without being recorded.

## What it costs

A change cost $0.44 to $0.56 at the median in the study's runs. Retries add some: an agent whose change breaks the build or the suite gets up to three tries.

| run | agent runs | about |
|---|---|---|
| first look: 20 changes, one `plain` and one `cleat` chain, visible probes once | 40 changes + 45 probes | $40 to $60 |
| the study's size: 90 changes, two chains per arm, both probe modes twice | 360 changes + about 300 probes | $300 to $500 |

The oracle, `doctor.py`, mutation and the card cost no tokens: they run your tests, not an agent. A first look takes an afternoon; the full size takes two or three days of machine time.

## The run

The commands below use these places; any will do.

```
~/replay/clones/acme__billing     your clone, named <owner>__<repo>
~/replay/work/billing             the project's work dir
~/replay/replay.json              your adapter
```

The work dir must sit outside the cleat checkout, with no `CLAUDE.md` or `AGENTS.md` in any directory above it: an agent loads every such file it finds, and the arms must see only your repository's own. Run every script from the cleat checkout, as `python3 study/replay/<script>`.

**1. Describe your stack.** Copy `replay.example.json` to `~/replay/replay.json` and fill it in. The required fields are `name`, `install`, `test`, `manifests` and `agent_commands`; `adapter.py`'s docstring explains each. The `test` command must run the files it is given (`{files}`) and write JUnit XML to `{out}`. Most runners can: pytest `--junitxml`, jest and vitest with a JUnit reporter, gotestsum, cargo-nextest, gradle, rspec.

**2. Check it.** `doctor.py ~/replay/replay.json ~/replay/clones/acme__billing` installs, runs your whole suite and prints how many tests it read. Fix what it names until it says `ready for a replay`. A few minutes.

**3. Pick the stretch.**

```
screen.py ~/replay/replay.json ~/replay/clones ~/replay/screen.json 20 1000 200000
```

The numbers are the stretch's length and the range of production lines it may start in. A change is usable when it adds at most 400 production lines, touches a test, and says what it does. A stretch where under half the changes are usable gives too few to score.

**4. Plan it.**

```
plan.py ~/replay/replay.json ~/replay/clones ~/replay/work/billing acme__billing ~/replay/screen.json 20
```

Every usable change is checked on your real history: its tests must fail before it and pass after it. Those are the scored changes. Checkpoints fall at every fifth of the stretch.

**5. The oracle, alone.**

```
chain.py ~/replay/clones ~/replay/work/billing 20 oracle-1
```

It must resolve at least 90% of the scored changes, or `chain.py` will not start the agent chains. Where it falls short, each step's `check-full.log` shows what failed. The fix goes in your adapter, never in the arms.

**6. The arms.**

```
chain.py ~/replay/clones ~/replay/work/billing 20 plain-1 cleat-1
```

Add `plain-2 cleat-2` for the study's size. A chain that stops can be started again with the same command: finished changes are not run again.

**7. Probes.** A fresh agent with no cleat makes the next real changes on each tree, so the code differs and the agent does not.

```
probe.py ~/replay/clones ~/replay/work/billing <checkpoint> 1 6 visible
```

Once per checkpoint. `1` is the repeats, `6` how many run at once.

**8. Planted bugs.** No tokens; your suite runs about 400 times per tree and test set.

```
mutate.py ~/replay/clones ~/replay/work/billing <checkpoint>
mutscore.py ~/replay/work/billing
```

**9. The numbers.** `analyze.py ~/replay/clones ~/replay/work billing` prints the report against the study's predictions, and caches your real history's measures for the card.

**10. Share them.** `share.py card ~/replay/work/billing --note "one line, if you want"` writes `study/contributed/<id>.json` and prints all of it. It names no repository, path, commit, date, command or code. Read it, then open a pull request with that one file. A run you stopped early shares as `stopped`, and is as welcome as a finished one.

## When something breaks

- **`doctor.py` reads no tests.** The test command must name `{files}` and `{out}`, and the runner must write JUnit XML there.
- **Many test files "reported no test".** The test ids do not start with the file they came from; a change would then look like it stopped tests running. Give the runner's JUnit output a `file` attribute, or name your test modules so their class names start with the file's path.
- **The oracle falls short.** Usually an install that needs the network, a test that depends on the machine, or a manifest missing from `manifests`, so a dependency change is not reinstalled.
- **`cleat's attach failed`.** The `cleat` chain stops rather than run without cleat. The message carries attach's own output.

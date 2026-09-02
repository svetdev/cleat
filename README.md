<p align="center"><img src="assets/cleat-mark.svg" width="160" alt="cleat: a horn cleat with two wraps of rope"></p>

# cleat

Quality gates for AI-driven development. Attach cleat to a project and every change an agent makes is held to ratchets that only tighten: functions that grow past a complexity ceiling, code copied instead of extracted, escapes that switch a check off, tests that stop running, references that cut across layers, public signatures that vanish. Existing debt is baselined once and never forgiven into the build.

A cam cleat holds a line against the pull and gives nothing back.

## Why gates, not instructions

An agent generates code faster than review can absorb it, and a rule in a context file is a suggestion: it holds until the model is deep in a long chain of steps and skips it. A gate is not a suggestion. It fails, names the file and the line, says what fixes it, and — wired into the agent's own loop — becomes the next thing the agent works on. Slowing generation down at the gate is the point; the compound interest on a mess is not cheap.

## Attach it

```
python3 /path/to/cleat/quality/bin/attach.py --into /path/to/your/project
```

One command, nothing to install. It copies `quality/` in, looks at the tree, and writes `quality.json` for what it finds (the languages, the documents an agent reads, the test trees, the escapes and duplication gates; complexity when `lizard` is installed, changed-line coverage when a report exists), the baselines so day one is green, the agent hooks, and a block in the agent's instructions file. Attaching again is safe: what exists is kept.

## Try it in two minutes

A scratch repository with one function, attached, then made worse:

```
mkdir try-cleat && cd try-cleat && git init -q
printf 'def f(a):\n    if a:\n        return 1\n    return 0\n' > app.py
python3 /path/to/cleat/quality/bin/attach.py --into .
python3 quality/bin/gate.py                        # gate: 3 gate(s), all passed.
printf 'x = f(1)  # type: ignore\n' >> app.py
python3 quality/bin/gate.py                        # FAIL  escapes ... app.py:5  type ignore
```

The failure names the file, the line and the escape, and says what fixes it. It does not say how to make the baseline accept it; that is a decision the config records, made by a person.

## The gates

| Gate | Refuses | Needs |
|---|---|---|
| doc-size | a document an agent reads growing past its ceiling | — |
| doc-citations | a document citing a file that is not there | — |
| test-hygiene | a test habit the suite was cleaned of growing back | — |
| escapes | a new `any`, `unwrap()`, `# type: ignore`, `.skip`, `\|\| true`… keyed by site | — |
| duplication | a copied block in the lines you changed; the duplicated share rising | — |
| guard-suites | a test suite on disk that nothing runs | — |
| layering | a reference from a lower layer to a higher one | imports, or ast-grep |
| reachability | a file matching a pattern that nothing references | imports, or ast-grep |
| complexity | a function over cyclomatic 8 or 60 lines | `lizard` or SwiftLint |
| public-api | a public signature removed, renamed or changed | — (or cargo-public-api, api-extractor) |
| manifests | a source file a generated project does not name | — |
| inventory | a directory that must not shrink losing an entry | — |
| sarif | a new result from any scanner that writes SARIF | the scanner |
| changed-coverage | changed lines the tests did not run | an LCOV or Cobertura report |
| crap | complexity the tests do not pay for | a coverage report |
| hotspots, mutation | reports: churn × complexity; mutants no test kills | — / a per-stack tool |

Nine languages have built-in escape patterns and conformance fixtures: Python, TypeScript/JavaScript, Swift, Rust, Go, Kotlin, Java, Ruby, shell. Every gate carries its own test, and every check is generic: a project's facts live in `quality.json` and nowhere else.

## How cleat attaches

Local by default. Attach writes the gates, the baselines and the agent hooks; nothing leaves your machine and nothing lands under `.github/`.

1. **Run it.** `python3 quality/bin/gate.py`. Green today, and from here it only tightens.
2. **Put it in the agent's loop.** The Stop hook hands a failing gate back to the agent as its next task, while the context that produced the code is still loaded; the PreToolUse guard refuses the policy edits an agent reaches for when blocked. Per project in `.claude/settings.json`, or globally with `[ -f quality/bin/gate.py ] && python3 quality/bin/gate.py --hook || true`. `--git-hooks` adds a pre-push hook for whoever works without an agent harness.
3. **Add CI when there is a reviewer, or when the agent pushes with your keys.** `attach.py --ci` writes the workflow and CODEOWNERS and prints the ruleset command that makes the check required on the default branch with code-owner review and no bypass. This is the only layer an agent cannot route around.

Local gives feedback and the guard; it cannot stop whoever holds the keyboard from removing the hook, which alone means stopping yourself. A protected branch holds only against an identity that cannot approve or bypass, so with `--ci` give the agent its own GitHub identity with contents and pull-request write and nothing more; then you are the reviewer.

## How a gate holds against an agent

1. **The ratchet is monotonic.** A baselined function that gets worse fails, not just a new one. A baseline looser than the code is a NOTE locally and a failure in CI, so the file always records exactly the debt that exists. Baselines carry their provenance, so a tool upgrade is noticed.
2. **A failure names the fix, never the escape.** No gate prints the command that accepts new debt beside the failure; it appears only where running it can only tighten.
3. **Policy is a person's.** The guard hook refuses `--write-baseline` and edits to `quality.json`, the baselines and the gates; CODEOWNERS makes the same true in review.

## Layout

```
quality/                the template — attach.py copies it into your repo
  bin/                  the gates; gate.py runs them; ratchet.py is the one
                        ratchet they share; attach.py attaches it all
    extractors/         fact-finding with no judgment in it: patterns,
                        complexity, coverage, duplication, references, …
  tests/                one suite per gate, plus conformance fixtures per
                        language; run.sh runs them all
  README.md             the adoption guide, key by key, with the tiers
  STRATEGY.md           why these gates, in this order
quality.json            cleat judged by its own gates
```

## Provenance

Extracted from a production codebase where the checks gated a Swift app, a Swift package and their tooling; generalized through a second adoption on a Rust/TypeScript project, then rebuilt around one engine and N extractors so a new language is a config entry, never a patch to a check. This repository is judged by its own gates, baselined debt included.

MIT licensed.

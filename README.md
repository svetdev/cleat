# cleat

Quality gates for AI-driven development. Attach cleat to a project and every change an agent makes is held to ratchets that only tighten: functions that grow past a complexity ceiling, code copied instead of extracted, escapes that switch a check off, tests that stop running, references that cut across layers, public signatures that vanish. Existing debt is baselined once and never forgiven into the build.

A cam cleat holds a line against the pull and gives nothing back.

## Why gates, not instructions

An agent generates code faster than review can absorb it, and a rule in a context file is a suggestion: it holds until the model is deep in a long chain of steps and skips it. A gate is not a suggestion. It fails, names the file and the line, says what fixes it, and — wired into the agent's own loop — becomes the next thing the agent works on. Slowing generation down at the gate is the point; the compound interest on a mess is not cheap.

## Attach it

```
python3 /path/to/cleat/quality/bin/attach.py --into /path/to/your/project
```

One command, nothing to install. It copies `quality/` in, looks at the tree, and writes:

- **`quality.json`** — the languages it found, the documents an agent reads with a word ceiling each, the test trees with today's fixed-sleep count as the ceiling, the escapes gate, the duplication ratchet; the complexity ratchet when `lizard` is installed, changed-line coverage when a coverage report exists.
- **The baselines** — today's debt, accepted once. Day one is green.
- **Agent hooks** — a Stop hook that runs the gates and hands a failure back to the agent as its next task, and a PreToolUse guard that refuses any command that would rewrite a baseline or edit the policy.
- **A block in the agent's instructions file** saying how the gates work and that a baseline is not a fix.
- **With `--ci`, the workflow and CODEOWNERS** — the gates under `--strict` on every pull request, and a person's review over the control plane. Local hooks give feedback; required CI gives authority.

Attaching again is safe: what exists is kept. `python3 quality/bin/gate.py` runs everything.

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

What local gives you is feedback and the guard. What it cannot do is stop whoever holds the keyboard from removing the hook, which for one person working alone means stopping yourself, and that is usually fine. A protected branch only holds against an identity that cannot approve or bypass, so with `--ci` give the agent its own GitHub identity with contents and pull-request write and nothing more; then you are the reviewer.

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

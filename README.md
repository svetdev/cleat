# cleat

Deterministic quality gates that only tighten. A cam cleat holds a line
against the pull and gives nothing back; these checks hold a codebase's
structure the same way — what's clean stays clean, and existing debt is
baselined, not forgiven into the build.

Built for codebases that AI agents work on. An agent generates code faster
than review can absorb it, and prompt instructions are suggestions — a rule
in a context file holds until the model is deep in a long chain of steps and
skips it. A gate is not a suggestion. The checks here refuse structural
regression mechanically: functions that grow past a complexity ceiling,
references that cut across layers, complexity the tests don't pay for, test
suites that quietly stop running, documentation that drifts from the code it
describes.

Slowing the agent down at the gate is the point. Generation is cheap; the
compound interest on a mess is not.

## Layout

```
quality/                the template — copy this directory into your repo
  bin/                  the checks and tools: generic, config-driven, no
                        project names inside
  tests/                one self-test per check, driven over a throwaway
                        tree; run.sh runs them all
  README.md             the adoption guide — travels with the copy, so it's
                        there for whoever finds the scripts vendored in
                        your repo
  STRATEGY.md           why these checks, the adoption ladder, per-stack
                        tool choices
  quality.example.json  this repo's own config, doubling as the working
                        example
quality.json            cleat judged by its own gates
```

## The idea, in three rules

1. **Ratchets, not gates.** Every check baselines what exists on adoption
   day and fails only new debt. Day one is always green; no project is too
   far gone to start.
2. **The project's facts live in one file** — `quality.json` at the
   repository root. The checks are generic; a check that learns a project's
   name has forked.
3. **Every check carries its own test.** A gate nobody trusts trains people
   to ignore red, which is worse than no gate.

What each check refuses, key by key, is in
[`quality/README.md`](quality/README.md); the reasoning and the adoption
ladder are in [`quality/STRATEGY.md`](quality/STRATEGY.md).

## Adopting it

1. Copy `quality/` into your repository.
2. Copy `quality/quality.example.json` to your repository root as
   `quality.json` and cut it down to the sections you're starting with —
   the ladder in STRATEGY.md says document ceilings and test hygiene first.
3. Write the baselines once (each check's `--write-baseline` or first-run
   findings).
4. Wire the checks into whatever runs your tests: the fast checks as a
   preflight, the coverage-fed CRAP check as a postflight.

The test runner itself stays yours — running a suite is the one part that
differs by stack, so the template ends at the preflight/postflight contract.

Works today with SwiftLint for Swift complexity, `lizard` for Python,
TypeScript, Rust and anything else it parses, and coverage from xccov,
llvm-cov, or istanbul. A new stack needs a coverage reader returning
per-declaration coverage — a page of code, not a port.

## With an agent in the loop

The gates compose with the practices that steer generation rather than
replacing them: a spec says what to build, tests verify behavior, gates
refuse structural rot — each catches what the others can't. For Claude
Code, wiring the preflight into a Stop or pre-push hook (and denying
`--no-verify`-style bypasses) puts the ratchet inside the agent's own
loop.

## Provenance

Extracted from [Kiteloop](https://github.com/svetdev), where the checks
gated a Swift app, a Swift package, and their Python/TypeScript tooling;
generalized through a second adoption on a Rust/TypeScript project. This
repository is judged by its own gates — `quality.json` at the root is the
config, baselined debt included.

MIT licensed.

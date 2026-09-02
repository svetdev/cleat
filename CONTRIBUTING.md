# Contributing

cleat is judged by its own gates, so the fastest way to understand what a change needs is to run them:

```
python3 quality/bin/gate.py --strict      # every gate this repository configures
bash quality/tests/run.sh                 # every check's own suite, about two minutes
```

Both must be green before a pull request. `pip install lizard ast-grep-cli` gets you the complexity gate and the ast-grep reader locally; without them the affected suites skip and say so.

## What a change looks like

- **Every check carries its own test.** A new gate or reader lands with a suite under `quality/tests/` that drives it over a throwaway tree, asserts the failure text as well as the success line, and writes nothing outside a temporary directory. The suites use `quality/tests/harness.py`.
- **The judgment lives in `quality/bin/ratchet.py`; the facts live in `quality/bin/extractors/`.** A gate is a thin script that reads its section of `quality.json`, asks an extractor for findings, and hands them to the engine. If you find yourself writing baseline comparison or exit-code logic in a check, it belongs in the engine.
- **A failure names the fix and never the command that accepts the debt.** That command appears only in the NOTE for tightening a baseline. Keep it that way.
- **Nothing in this repository names another project.** Fixtures use placeholders (`Acme`, `scripts/tools`); anecdotes say "the pilot codebase". Fixture trees for tests are made up, not copied.
- **The gates catch your own code.** A function over cyclomatic 8 or 60 lines fails; split it rather than baselining it. Copied test boilerplate fails the duplication gate; extend the harness instead. Baseline rewrites are a policy change and go in their own commit with the reason.

## Adding a language

1. An escapes entry in `check-escapes.py`: suffixes and the patterns worth a site each.
2. A fixture under `quality/tests/fixtures/<language>/` with known escape sites, decoys included, and a `branchy` function of cyclomatic 5; an entry in `test-conformance.py`.
3. If the language has imports, a resolver in `extractors/references.py`; if ast-grep parses it, its declaration and identifier node kinds in the same file.
4. A row in `quality/README.md` where the language matters.

## Adding a gate

An extractor (or a reader for a tool's report), the check script, its suite, a row in both READMEs, and a line in `gate.py`'s table so the runner and `attach.py` know it. Prefer reading a report an existing tool writes over owning a scanner.

## Reporting

The issue forms ask for the gate, the language, and a minimal snippet that reproduces what you saw. A false positive with a ten-line fixture gets fixed in a day; a description of one takes a week of guessing.

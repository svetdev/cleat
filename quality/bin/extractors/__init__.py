"""extractors — the fact-finding half of every gate, with no judgment in it.

A gate is a universal judgment (a ceiling, a ratchet, an allowlist) applied to
facts an extractor found. The judgment lives in `ratchet.py` and is the same
everywhere; the extractor is what differs by gate, and only two kinds of
extractor touch a parser at all: per-function complexity (`complexity.py`:
lizard or SwiftLint) and declarations-and-references (`references.py`:
declared names for Swift, imports elsewhere). Everything in this directory
needs none:

  wordcount   words in a document                            check-doc-size
  patterns    regex sites in code, per file and per line     check-test-hygiene, check-escapes
  churn       commits per file from git log                  report-hotspots
  changed     lines changed against a base, from git diff     check-duplication, check-changed-coverage
  duplication copied blocks, found here or read from jscpd    check-duplication
  coverage    LCOV, Cobertura, xccov, llvm-cov, istanbul      check-crap, check-changed-coverage
  complexity  per-function cyclomatic and length              check-complexity, check-crap, report-hotspots
  references  which file depends on which                     check-layering, check-reachability
  sarif       results from any scanner that writes SARIF      check-sarif

Each returns plain values or `ratchet.Finding`s and knows nothing about
ceilings, baselines or exit codes. A new language is a config entry naming
which extractor to run over which files — never a patch to a check.
"""

#!/usr/bin/env python3
"""check-complexity — the complexity ratchet: a production function over the
cyclomatic ceiling or the length ceiling fails, beyond the ones the baseline
already holds — and a baselined one that grew fails too.

Agents write long functions and do not notice; neither does a reviewer reading
a diff, since the function was already long before the diff. A threshold a tool
enforces is the one kind of rule an agent cannot soften.

The reader is `extractors/complexity.py`: lizard for Python, TypeScript, Rust,
Go, Kotlin, Java, Ruby, Swift and more, or SwiftLint for Swift natively. The
ratchet is `ratchet.py`: functions over the gate on adoption day are in the
baseline, keyed by file and declaration text so a shifted line still matches,
with the numbers they had; a new one fails, a baselined one that grew fails,
one that improved or vanished is a NOTE — and a --strict failure, which is how
CI keeps the baseline exactly as loose as the code.

Everything that names the project is the `complexity` section of quality.json
(`complexity_lizard` is read as the same thing, for configs written before the
two gates were one):

  "complexity": {
    "tool":      "lizard",                        # or "swiftlint"; lizard when "languages" is given
    "sources":   ["apps/api/src", "apps/web/src"],
    "languages": ["rust", "typescript"],          # lizard -l values
    "exclude":   ["*.test.ts"],                   # lizard -x globs
    "exclude_except": ["apps/api/src/test-runner.ts"],   # production paths an exclude glob would drop by name
    "skip_rust_tests": true,                      # drop `#[cfg(test)]` modules
    "ceilings":  {"cc": 8, "lines": 60},
    "baseline":  "quality/complexity-baseline.json"
  }

  quality/bin/check-complexity.py
  quality/bin/check-complexity.py --quiet
  quality/bin/check-complexity.py --write-baseline   # accept what is over the gate today
  quality/bin/check-complexity.py --strict           # CI: a loose baseline fails too
  quality/bin/check-complexity.py --csv FILE         # judge a saved lizard CSV (the tests use this)
  quality/bin/check-complexity.py --lint FILE        # judge a saved SwiftLint JSON report
"""

import argparse
import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
import ratchet
from extractors import complexity

SECTIONS = ("complexity", "complexity_lizard")
RETIRED_KEYS = ("cwd", "config")   # the SwiftLint-native gate's shape, before the two gates were one


def is_retired_shape(section):
    return any(k in section for k in RETIRED_KEYS)


def section_of(config):
    """The complexity section, under either name — KeyError naming what is missing, what is
    retired, or the pair when both names configure a live gate."""
    present = [s for s in SECTIONS if isinstance(config.data.get(s), dict)]
    if not present:
        raise KeyError("%s has no \"complexity\" section — see quality.example.json" % config.file)
    live = [s for s in present if not is_retired_shape(config.data[s])]
    if len(live) > 1:
        raise KeyError("%s: both \"complexity\" and \"complexity_lizard\" configure this gate — keep one (they are the same section)" % config.file)
    if not live:
        raise KeyError("%s: \"%s\" is in the retired SwiftLint-native shape (cwd/config/a SwiftLint baseline). "
                       "Set \"tool\": \"swiftlint\", keep \"sources\" and \"ceilings\", point \"baseline\" at a new file "
                       "and write it with --write-baseline — or run attach.py --refresh, which rewrites it." % (config.file, present[0]))
    return live[0], config.data[live[0]]


def judge(functions, cc_ceiling, line_ceiling, repo):
    """[(repo-relative file, line, text, cc, length)] for every function over either ceiling."""
    repo = os.path.realpath(repo)
    over = []
    for f in functions:
        if f.cc > cc_ceiling or f.length > line_ceiling:
            over.append((os.path.relpath(f.path, repo), f.line, complexity.declaration_text(f.path, f.line), f.cc, f.length))
    over.sort(key=lambda o: (-max(o[3] / cc_ceiling, o[4] / line_ceiling), o[0], o[1]))
    return over


def read_functions(args, name, section, config):
    """(functions, skipped, tool, version) from a saved report when a flag names one, else
    from the tool over the configured sources."""
    if args.csv:
        with open(args.csv) as handle:
            functions, skipped = complexity.functions_from_csv(handle.read(), skip_rust_tests=section.get("skip_rust_tests", True))
        return functions, skipped, "lizard", None
    if args.lint:
        with open(args.lint) as handle:
            return complexity.functions_from_swiftlint(json.load(handle)), 0, "swiftlint", None
    roots = config.paths(config.get(name, "sources"))
    if args.only is not None:
        roots = [config.path(f) for f in args.only if os.path.isfile(config.path(f))]
        if not roots:
            return [], 0, complexity.tool_of(section), None
    return complexity.measure(section, roots, config.paths(section.get("exclude_except", [])))



def main():
    parser = argparse.ArgumentParser(description="complexity ratchet over the configured sources")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--csv", help="a saved lizard --csv output to judge instead of running a tool")
    parser.add_argument("--lint", help="a saved SwiftLint JSON report to judge instead of running a tool")
    parser.add_argument("--repo", help="paths are reported relative to this (default: the directory of quality.json)")
    ratchet.add_only_argument(parser)
    ratchet.add_strict_argument(parser)
    quality_config.add_config_argument(parser)
    args = parser.parse_args()

    config = quality_config.load(args.config)
    try:
        name, section = section_of(config)
        ceilings = config.get(name, "ceilings")
        baseline_path = config.path(config.get(name, "baseline"))
        cc_ceiling, line_ceiling = int(ceilings["cc"]), int(ceilings["lines"])
        functions, skipped, tool, version = read_functions(args, name, section, config)
    except (KeyError, complexity.ToolError) as problem:
        print("FAIL: %s" % (problem.args[0] if problem.args else problem), file=sys.stderr)
        return 2

    repo = os.path.abspath(args.repo) if args.repo else config.root
    over = [ratchet.Finding(f, line, text, {"cc": cc, "lines": n})
            for f, line, text, cc, n in judge(functions, cc_ceiling, line_ceiling, repo)]
    measured = ratchet.provenance(tool, version, {k: section[k] for k in sorted(section) if k != "baseline"})

    if args.write_baseline:
        ratchet.write(baseline_path, over, measured)
        print("baseline written: %d function(s) over the gate (cyclomatic > %d or body > %d lines)"
              % (len(over), cc_ceiling, line_ceiling))
        return 0

    entries, stored = ratchet.read(baseline_path)
    over, entries = ratchet.restrict(over, entries, args.only)
    verdict = ratchet.judge(over, entries, ["cc", "lines"], stored, measured)
    gate = ratchet.Gate(
        noun="production function(s)",
        over="over the complexity gate (cyclomatic > %d or body > %d lines)" % (cc_ceiling, line_ceiling),
        fix="Split the function so each piece is under the gate. Accepting new debt into the baseline is a "
            "policy decision for a person, not a fix — see quality/README.md.",
        remedy="quality/bin/check-complexity.py --write-baseline",
        show=lambda v: "cc %s, %s lines" % (v["cc"], v["lines"]))
    ok_line = ("OK: %d functions judged (%d inline tests skipped), %d over the gate, all %d in the baseline"
               % (len(functions), skipped, len(over), len(over)))
    return ratchet.report(verdict, gate, len(entries), ok_line, quiet=args.quiet, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())

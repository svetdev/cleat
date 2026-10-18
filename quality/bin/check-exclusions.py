#!/usr/bin/env python3
"""check-exclusions — fail on a source file newly marked as generated.

`gate.py --summary` sizes each part's debt against its production lines, and a
file whose header says a tool generated it (`@generated`, `DO NOT EDIT`, `Code
generated … DO NOT EDIT`, `auto-generated`) is not production code: it is left
out of the counts. That makes the header a way out of being measured — a line
anyone can type — so the files carrying one are ratcheted like an escape: the
baseline records the generated files the project has, a new one fails until a
person accepts it, and one that is no longer generated (or gone) is dropped by
`--tighten`. A real generator's output is accepted once; a header typed onto
hand-written code is the thing this catches.

    "exclusions": {
        "skip_dirs": ["quality"],                          # beside the usual build and dependency directories
        "baseline":  "quality/exclusions-baseline.json"    # the ratchet
    }

  quality/bin/check-exclusions.py                   # gate (exit 1 on a newly generated file)
  quality/bin/check-exclusions.py --write-baseline  # a person accepts every generated file there is today
  quality/bin/check-exclusions.py --tighten         # drop the files no longer generated; never adds
"""

import argparse
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
import ratchet
from extractors import census

SECTION = "exclusions"


def findings(section, config):
    """One finding per source file whose header says it was generated, with its lines."""
    generated = {}
    census.take(config.root, set(section.get("skip_dirs", [])) | {".claude", ".github"}, generated)
    return [ratchet.Finding(rel, 1, "generated", {"language": language, "lines": lines})
            for rel, (language, lines) in sorted(generated.items())]


def main():
    parser = argparse.ArgumentParser(description="fail on a source file newly marked as generated")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    ratchet.add_tighten_argument(parser)
    ratchet.add_strict_argument(parser)
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    config = quality_config.load(args.config)
    try:
        section = config.section(SECTION)
        baseline_path = config.path(config.get(SECTION, "baseline"))
    except KeyError as problem:
        print("FAIL: %s" % problem.args[0], file=sys.stderr)
        return 2
    found = findings(section, config)
    measured = ratchet.provenance("exclusions", "1", {k: section[k] for k in sorted(section) if k != "baseline"})
    if args.write_baseline:
        ratchet.write(baseline_path, found, measured)
        print("baseline written: %d generated file(s) accepted" % len(found))
        return 0
    entries, stored = ratchet.read(baseline_path)
    verdict = ratchet.judge(found, entries, [], stored, measured)
    gate = ratchet.Gate(
        noun="generated file(s)",
        over="— a header saying a tool wrote the file, which leaves it out of the summary's counts",
        fix="If a generator wrote it, a person accepts it into the baseline; if it was written by hand, remove the "
            "header so the file is measured like the rest. Accepting is a policy decision for a person — see quality/README.md.",
        remedy="quality/bin/check-exclusions.py --tighten",
        show=lambda v: "%d lines" % v.get("lines", 0))
    ok_line = "OK: %d generated file(s), all in the baseline" % len(found)
    return ratchet.report(verdict, gate, len(entries), ok_line, quiet=args.quiet, strict=args.strict,
                          tighten=args.tighten, baseline=(baseline_path, measured, []))


if __name__ == "__main__":
    sys.exit(main())

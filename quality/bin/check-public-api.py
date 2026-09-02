#!/usr/bin/env python3
"""check-public-api — fail when a public signature disappears or changes.

For a distributed library an accidental break is the costliest defect it can
ship, and it is exactly what an agent tidying a file produces: a parameter
renamed, a function made private, a type folded into another. The baseline
is the public surface — every exported signature — recorded once. A run
whose surface lacks a recorded signature fails: removed, renamed or changed,
it is a break until a person says otherwise. A signature the baseline lacks
is an addition: a NOTE, and under --strict a failure, so the recorded surface
is always exactly what ships. Recording a change is `--write-baseline`, under
CODEOWNERS review — which is how a break becomes a deliberate release.

The surface comes from the built-in reader (`extractors/surface.py`: what the
language marks as exported) or from a tool that knows the language properly —
`cargo public-api`'s output or api-extractor's `.api.md` report.

  "public_api": [
    {"name": "sdk", "language": "typescript", "roots": ["packages/sdk/src"],
     "baseline": "quality/api-sdk.json"},
    {"name": "core", "report": {"cargo-public-api": "target/public-api.txt"},
     "baseline": "quality/api-core.json"}
  ]

  quality/bin/check-public-api.py --gate sdk
  quality/bin/check-public-api.py --gate sdk --write-baseline   # record the surface, or a deliberate change
  quality/bin/check-public-api.py --gate sdk --strict           # CI: an unrecorded addition fails too
"""

import argparse
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
import ratchet
from extractors import surface

SECTION = "public_api"
READERS = {"cargo-public-api": surface.from_cargo_public_api, "api-extractor": surface.from_api_extractor}


def findings(entry, config):
    """[Finding] for the surface `entry` describes, keyed by file and signature."""
    report = entry.get("report", {})
    if report:
        kind, path = next(iter(report.items()))
        if kind not in READERS:
            raise KeyError("no public-API reader for %r — one of: %s" % (kind, ", ".join(READERS)))
        with open(config.path(path)) as handle:
            rows = READERS[kind](handle.read())
    else:
        language = entry.get("language")
        if language not in surface.EXPORTED:
            raise KeyError("no built-in public-API reader for %r — one of: %s" % (language, ", ".join(sorted(surface.EXPORTED))))
        rows = surface.built_in(config.paths(entry.get("roots", ["."])), language, config.root, set(entry.get("skip_dirs", [])))
    seen = {}
    for rel, line, sig in rows:
        seen.setdefault((rel, sig), line)
    return [ratchet.Finding(rel, line, sig, {}) for (rel, sig), line in sorted(seen.items(), key=lambda kv: (kv[0][0], kv[1]))]


def print_break(stale, name):
    print("FAIL: %d public signature(s) of %s recorded in the baseline are gone — removed, renamed or changed, "
          "which is a breaking change until a person records it:" % (len(stale), name))
    for e in stale[:30]:
        print("  %s  %s" % (e["file"], e["text"]))
    print("Restore the signature, or keep the old one as a deprecated alias. Shipping the break is a release "
          "decision for a person — see quality/README.md.")


def print_additions(new, name, remedy):
    print("NOTE: %d public signature(s) of %s not yet recorded — additions, until recorded:" % (len(new), name))
    for f in new[:30]:
        print("  %s:%d  %s" % (f.file, f.line, f.text))
    if remedy:
        print("Record them: %s" % remedy)


def _print_notes(verdict, name, remedy):
    if verdict.new:
        print_additions(verdict.new, name, None if verdict.stale else remedy)
    if verdict.drift:
        print("NOTE: %s" % verdict.drift)


def report(verdict, name, baseline_size, quiet, strict, remedy):
    """Removed or changed signatures fail; additions are a NOTE, a --strict failure."""
    if verdict.stale:
        print_break(verdict.stale, name)
        _print_notes(verdict, name, remedy)
        return 1
    _print_notes(verdict, name, remedy)
    loose = bool(verdict.new or verdict.drift)
    if loose and strict:
        print("FAIL: the recorded surface differs from the code — under --strict it must match exactly.")
        return 1
    if not loose and not quiet:
        print("OK: %s — all %d recorded public signature(s) still there" % (name, baseline_size))
    return 0


def main():
    parser = argparse.ArgumentParser(description="fail when a public signature disappears or changes")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--gate", help="which entry of the public_api list")
    ratchet.add_strict_argument(parser)
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    config = quality_config.load(args.config)
    try:
        entry = config.entry(SECTION, args.gate)
        baseline_path = config.path(entry["baseline"])
        found = findings(entry, config)
    except (KeyError, OSError) as problem:
        print("FAIL: %s" % (problem.args[0] if problem.args else problem), file=sys.stderr)
        return 2
    name = entry.get("name") or "public-api"
    measured = ratchet.provenance("public-api", None, {k: v for k, v in entry.items() if k != "baseline"})
    if args.write_baseline:
        ratchet.write(baseline_path, found, measured)
        print("baseline written: %d public signature(s) of %s recorded" % (len(found), name))
        return 0
    entries, stored = ratchet.read(baseline_path)
    verdict = ratchet.judge(found, entries, [], stored, measured)
    remedy = "quality/bin/check-public-api.py%s --write-baseline" % (" --gate %s" % name if args.gate else "")
    return report(verdict, name, len(entries), args.quiet, args.strict, remedy)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""check-inventory — fail when a directory that must not shrink loses an entry.

Some directories are registries a build depends on and a tool rewrites
wholesale: a per-crate `.sqlx` query cache, where preparing one workspace
member wipes another's entries; a snapshot directory; generated fixtures. The
loss is silent until a later build fails somewhere else. This records the
entries once and fails when one is gone. New entries are a NOTE — and a
--strict failure, so record them in the same commit that adds them.

  "inventory": [
    {"name": "sqlx", "path": ".sqlx", "pattern": "query-*.json", "baseline": "quality/inventory-sqlx.json"}
  ]

  quality/bin/check-inventory.py --gate sqlx
  quality/bin/check-inventory.py --gate sqlx --write-baseline
"""

import argparse
import fnmatch
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
import ratchet

SECTION = "inventory"


def findings(entry, config):
    """One Finding per entry under the directory matching the pattern, keyed by its path."""
    directory = config.path(entry["path"])
    if not os.path.isdir(directory):
        raise KeyError("no such directory: %s" % entry["path"])
    pattern = entry.get("pattern", "*")
    out = []
    for dirpath, _dirs, names in os.walk(directory):
        for name in sorted(names):
            if fnmatch.fnmatch(name, pattern):
                out.append(ratchet.Finding(os.path.relpath(os.path.join(dirpath, name), config.root), 0, name, {}))
    return sorted(out, key=lambda f: f.file)


def plural(n, one, many):
    return one if n == 1 else many


def print_gone(stale, name, path):
    print("FAIL: %d %s of %s recorded under %s %s gone — a tool rewrote the directory, or a file was deleted:"
          % (len(stale), plural(len(stale), "entry", "entries"), name, path, plural(len(stale), "is", "are")))
    for e in stale[:30]:
        print("  %s" % e["file"])
    print("Restore them (re-run what produced them, for every member that needs them). Dropping an entry for good "
          "is recorded by a person — see quality/README.md.")


def print_new(new, path, remedy, strict):
    print("NOTE: %d new %s under %s not yet recorded:" % (len(new), plural(len(new), "entry", "entries"), path))
    for f in new[:30]:
        print("  %s" % f.file)
    print("Record them: %s" % remedy)
    if strict:
        print("FAIL: the recorded inventory differs from the directory — under --strict it must match exactly.")


def report(verdict, name, path, baseline_size, quiet, strict, remedy):
    if verdict.stale:
        print_gone(verdict.stale, name, path)
        return 1
    if verdict.new:
        print_new(verdict.new, path, remedy, strict)
        return 1 if strict else 0
    if not quiet:
        print("OK: %s — all %d recorded %s under %s still there" % (name, baseline_size, plural(baseline_size, "entry", "entries"), path))
    return 0


def main():
    parser = argparse.ArgumentParser(description="fail when a directory that must not shrink loses an entry")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--gate", help="which entry of the inventory list")
    ratchet.add_strict_argument(parser)
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    config = quality_config.load(args.config)
    try:
        entry = config.entry(SECTION, args.gate)
        baseline_path = config.path(entry["baseline"])
        found = findings(entry, config)
    except KeyError as problem:
        print("FAIL: %s" % problem.args[0], file=sys.stderr)
        return 2
    name = entry.get("name") or "inventory"
    measured = ratchet.provenance("inventory", None, {k: v for k, v in entry.items() if k != "baseline"})
    if args.write_baseline:
        ratchet.write(baseline_path, found, measured)
        print("baseline written: %d entr%s under %s recorded" % (len(found), "y" if len(found) == 1 else "ies", entry["path"]))
        return 0
    entries, stored = ratchet.read(baseline_path)
    verdict = ratchet.judge(found, entries, [], stored, measured)
    remedy = "quality/bin/check-inventory.py%s --write-baseline" % (" --gate %s" % name if args.gate else "")
    return report(verdict, name, entry["path"], len(entries), args.quiet, args.strict, remedy)


if __name__ == "__main__":
    sys.exit(main())

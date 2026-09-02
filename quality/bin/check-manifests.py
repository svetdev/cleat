#!/usr/bin/env python3
"""check-manifests — fail on a source file a generated project file does not name.

An Xcode project generated from a tree (xcodegen, tuist, a script) is a
snapshot: a test file added to disk after the last generation compiles into
nothing and runs in nothing, and the suite stays green because it never ran.
A modification time cannot catch this — a fresh checkout gives every file the
same one — but the project file's own text can: every source under the roots
must be named in it. That is what this reads. It applies to any manifest that
lists files: a `project.pbxproj`, a CMakeLists, a hand-kept file list.

  "manifests": [
    {"file": "App.xcodeproj/project.pbxproj", "roots": ["AppTests", "AppUITests"], "extensions": [".swift"],
     "exempt": {"AppTests/Fixtures/Sample.swift": "a fixture, compiled by nothing on purpose"}}
  ]

  quality/bin/check-manifests.py
  quality/bin/check-manifests.py --quiet
"""

import argparse
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
from extractors import patterns

SECTION = "manifests"
SKIP_DIRS = {".git", "node_modules", "build", ".build", "DerivedData", "__pycache__"}


def unnamed(manifest_text, sources, repo_root):
    """The repo-relative sources whose basename the manifest does not mention."""
    return [os.path.relpath(path, repo_root) for path in sources if os.path.basename(path) not in manifest_text]


def sources_of(entry, config):
    roots = config.paths(entry.get("roots", ["."]))
    skip = SKIP_DIRS | set(entry.get("skip_dirs", []))
    return list(patterns.files(roots, tuple(entry.get("extensions", [".swift"])), skip))


def judge(entry, config):
    """(unnamed sources, dead exemptions, judged count, exempt count) for one manifest."""
    with open(config.path(entry["file"]), errors="replace") as handle:
        text = handle.read()
    sources = sources_of(entry, config)
    exempt = entry.get("exempt", {})
    missing = [rel for rel in unnamed(text, sources, config.root) if rel not in exempt]
    known = {os.path.relpath(p, config.root) for p in sources}
    dead = [rel for rel in sorted(exempt) if rel not in known or os.path.basename(rel) in text]
    return missing, dead, len(sources), len(set(exempt) & known)


def print_missing(shown, missing):
    print("FAIL: %s does not name %d source file(s) under its roots — compiled into nothing, run by nothing:" % (shown, len(missing)))
    for rel in missing[:30]:
        print("  %s" % rel)
    print("Regenerate the project, or add the file to its target. A file that is compiled by nothing on purpose "
          "goes under \"exempt\" with the reason.")


def print_dead(shown, dead):
    print("FAIL: %d exempt entr%s of %s name%s a file that is gone or now in the manifest — delete the entry:"
          % (len(dead), "y" if len(dead) == 1 else "ies", shown, "s" if len(dead) == 1 else ""))
    for rel in dead:
        print("  %s" % rel)


def report(shown, missing, dead, judged, exempt, quiet):
    """Print one manifest's result; True when it failed."""
    if missing:
        print_missing(shown, missing)
    if dead:
        print_dead(shown, dead)
    if not missing and not dead and not quiet:
        print("OK: %s names all %d source file(s) under its roots (%d exempt)" % (shown, judged, exempt))
    return bool(missing or dead)


def main():
    parser = argparse.ArgumentParser(description="fail on a source file a generated project does not name")
    parser.add_argument("--quiet", action="store_true")
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    config = quality_config.load(args.config)
    try:
        entries = config.section(SECTION)
        if not isinstance(entries, list):
            raise KeyError("%s: \"%s\" must be a list of {\"file\", \"roots\"} entries" % (config.file, SECTION))
        failed = False
        for entry in entries:
            if not os.path.isfile(config.path(entry["file"])):
                raise KeyError("no such manifest: %s" % entry["file"])
            failed = report(entry["file"], *judge(entry, config), quiet=args.quiet) or failed
    except (KeyError, OSError) as problem:
        print("FAIL: %s" % (problem.args[0] if problem.args else problem), file=sys.stderr)
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""unreached — files nothing else references, on each arm's tree at the end of a replay.

  unreached.py <replay work dir> <language> <imports | ast-grep> <root> [<root> …]

For the last checkpoint every chain reached: the production files `check-reachability.py`
would report with every file under the roots held to being reached (`"pattern": "*"`), on the
oracle's tree (upstream's own code) and on each agent chain's, and for the agent chains
those upstream's tree does not report. A file an agent wrote that nothing imports is
finished work that does nothing, which is what the gate exists to catch. Entry points are
reported on every tree alike, so only the difference from upstream is read. Tests are left
out. No agent and no tokens; `ast-grep` needs ast-grep on the PATH.
"""

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(os.path.dirname(HERE)), "quality", "bin")
sys.path.insert(0, BIN)
import quality_config   # noqa: E402
import summary          # noqa: E402

_spec = importlib.util.spec_from_file_location("reachability", os.path.join(BIN, "check-reachability.py"))
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)
EXCLUDE = ["*.d.ts", "*.test.*", "*.spec.*", "*/tests/*", "tests/*", "*/__tests__/*", "*Tests.swift", "*Test.kt"]


def reported(tree, language, reader, roots):
    path = os.path.join(tree, ".unreached-study.json")
    with open(path, "w") as handle:
        json.dump({"reachability": {"roots": roots, "pattern": "*", "references": reader, "language": language,
                                    "exclude": EXCLUDE, "exempt": {}}}, handle)
    try:
        config = quality_config.load(path)
        unreached, _, judged, _ = check.judge(check.Rules(config), config)
    finally:
        os.remove(path)
    return {f for f in unreached if not summary._is_test(f)}, judged


def main(work, language, reader, *roots):
    chains = os.path.join(work, "chains")
    arms = sorted(a for a in os.listdir(chains) if os.path.isdir(os.path.join(chains, a, "snapshots")))
    points = [set(p for p in os.listdir(os.path.join(chains, a, "snapshots")) if p.isdigit()) for a in arms]
    last = str(max(int(p) for p in set.intersection(*points)))
    got = {a: reported(os.path.abspath(os.path.join(chains, a, "snapshots", last)), language, reader, list(roots)) for a in arms}
    upstream = next((got[a][0] for a in arms if a.startswith("oracle")), set())
    print("%s at change %s" % (os.path.basename(os.path.normpath(work)), last))
    for arm in arms:
        files, judged = got[arm]
        new = sorted(files - upstream)
        print("  %-10s %4d files judged, %3d unreached, %2d of them not unreached upstream%s" % (
            arm, judged, len(files), len(new), "" if arm.startswith("oracle") or not new else ": " + ", ".join(new[:8])))


if __name__ == "__main__":
    main(*sys.argv[1:])

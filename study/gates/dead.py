#!/usr/bin/env python3
"""dead — what the dead-symbols check reports on each arm's tree at the end of a replay.

  dead.py <replay work dir> <language> <root> [<root> …]      (roots relative to the tree, e.g. src)

For the last checkpoint every chain reached: the symbols `check-dead-symbols.py` would report
on the oracle's tree (upstream's own code) and on each agent chain's, and for the agent
chains those upstream does not have, split into private and exported or `pub`. A private
symbol nothing references and upstream never had is dead code an agent left. Needs ast-grep
on the PATH; no agent and no tokens.
"""

import collections
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(os.path.dirname(HERE)), "quality", "bin")
sys.path.insert(0, BIN)
import quality_config   # noqa: E402

_spec = importlib.util.spec_from_file_location("dead_symbols", os.path.join(BIN, "check-dead-symbols.py"))
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)
EXCLUDE = ["*.d.ts", "*.test.ts", "*.test.tsx", "*/tests/*", "*/__tests__/*", "*Tests.swift", "*Test.kt"]
PUBLIC = ("pub", "export", "public", "open")


def reported(tree, language, roots):
    """{(file, name): 'public' | 'private'} for the symbols the check would report."""
    path = os.path.join(tree, ".dead-study.json")
    with open(path, "w") as handle:
        json.dump({"dead_symbols": {"roots": roots, "language": language, "exclude": EXCLUDE}}, handle)
    try:
        config = quality_config.load(path)
        dead, _ = check.dead_among(check.Rules(config), config)
    finally:
        os.remove(path)
    out = {}
    for rel, line, name in dead:
        text = open(os.path.join(tree, rel), errors="replace").read().split("\n")[line - 1].strip()
        out[(rel, name)] = "public" if text.startswith(PUBLIC) else "private"
    return out


def main(work, language, *roots):
    chains = os.path.join(work, "chains")
    arms = sorted(a for a in os.listdir(chains) if os.path.isdir(os.path.join(chains, a, "snapshots")))
    points = [set(p for p in os.listdir(os.path.join(chains, a, "snapshots")) if p.isdigit()) for a in arms]
    last = str(max(int(p) for p in set.intersection(*points)))
    got = {a: reported(os.path.abspath(os.path.join(chains, a, "snapshots", last)), language, list(roots)) for a in arms}
    upstream = next((got[a] for a in arms if a.startswith("oracle")), {})
    print("%s at change %s" % (os.path.basename(os.path.normpath(work)), last))
    for arm in arms:
        new = {k: v for k, v in got[arm].items() if k not in upstream}
        print("  %-10s reported %3d %s | not in upstream %3d %s" % (
            arm, len(got[arm]), dict(collections.Counter(got[arm].values())), len(new), dict(collections.Counter(new.values()))))
        private = sorted(name for (_, name), kind in new.items() if kind == "private")
        if private and not arm.startswith("oracle"):
            print("             private and not upstream's:", ", ".join(private[:12]))


if __name__ == "__main__":
    main(*sys.argv[1:])

#!/usr/bin/env python3
"""tsproof — proof.py for the TypeScript tasks: before any agent runs, each arm's gates shown to judge them correctly.

  tsproof.py <clones dir> <work dir> [jobs] [task id …]        (arms from STUDY_ARMS, as tsrun.py)

The steps and what they prove are proof.py's. What differs is the tree (tsrun's), which files
are tests, and the planted code: a branching TypeScript function nothing calls, and for a
coverage or CRAP arm the same function beside a test file, named as the task's own are, that
walks every branch of it.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import proof    # noqa: E402
import run      # noqa: E402
import tsdraw   # noqa: E402
import tsrun    # noqa: E402

UNTESTED = '''

export function proofPickKind(value: unknown, strict: boolean, fallback: string): string {
  if (value === null || value === undefined) {
    return fallback
  }
  if (typeof value === 'string' && strict) {
    return 'text'
  }
  if (typeof value === 'number') {
    return value >= 0 ? 'number' : 'negative'
  }
  return fallback
}
'''
PAYS = """%simport { proofPickKind } from '%s'

test('proof pick kind', () => {
  const got = [proofPickKind(null, true, 'x'), proofPickKind('a', true, 'x'), proofPickKind('a', false, 'x'),
    proofPickKind(1, true, 'x'), proofPickKind(-1.5, true, 'x')]
  if (got.join() !== 'x,text,x,number,negative') throw new Error(got.join())
})
"""
TANGLED = ("\n\nexport function proofTangled(a: number, b: boolean, c: number): number {\n"
           + "".join("  if (a === %d && b) {\n    c += %d\n  }\n" % (n, n) for n in range(12)) + "  return c\n}\n")


def target_of(task, root):
    """The first TypeScript production file the commit modifies that exists at its parent."""
    return next((f[2] for f in task["files"] if f[0] == "M" and f[2].endswith((".ts", ".tsx")) and not f[2].endswith(".d.ts")
                 and tsdraw.production(f[2]) and os.path.isfile(os.path.join(root, f[2]))), None)


def paying_test(task, root, target):
    """A test file beside the task's first one, named as it is, that imports the planted function."""
    first = task["tests"][0]
    where = os.path.join(root, os.path.dirname(first))
    os.makedirs(where, exist_ok=True)
    runner, _ = tsdraw.locate(root, first)
    module = os.path.relpath(os.path.join(root, re.sub(r"\.tsx?$", "", target)), where)
    module = module if module.startswith(".") else "./" + module
    with open(os.path.join(where, "proofPaid" + os.path.basename(first)[os.path.basename(first).index("."):]), "w") as handle:
        handle.write(PAYS % ("import { test } from 'vitest'\n" if runner == "vitest" else "", module))


def plant(task, arm, root):
    """(what must refuse, problem or None), as proof.plant: the arm's own violation refused by
    its own gate, and for a coverage or CRAP arm the same function passed once a test pays for it."""
    name = arm.split("-")[0]
    reads_coverage = name in run.COVERAGE_ARMS | run.CRAP_ARMS
    target = target_of(task, root)
    if target is None:
        return None, "the commit changes no TypeScript production file that exists at its parent"
    with open(os.path.join(root, target), "a") as handle:
        handle.write(UNTESTED if reads_coverage else TANGLED)
    wanted = [g for g, arms in (("crap", run.CRAP_ARMS), ("changed-coverage", run.COVERAGE_ARMS)) if name in arms] or ["complexity"]
    _, said, failing = proof.gates(root)
    proof.restore(root)
    missing = [g for g in wanted if g not in failing]
    if missing or not reads_coverage:
        return wanted, ("the planted violation was not refused by %s: %s" % (", ".join(missing), said[-300:]) if missing else None)
    with open(os.path.join(root, target), "a") as handle:
        handle.write(UNTESTED)
    paying_test(task, root, target)
    _, said, failing = proof.gates(root)
    proof.restore(root)
    still = [g for g in wanted if g in failing]
    return wanted, ("the same function with every branch tested is still refused by %s: %s" % (", ".join(still), said[-400:]) if still else None)


proof.TEST = re.compile(r"(^|/)(__tests__|tests?|e2e)/|\.(test|spec)\.[cm]?[jt]sx?$")
proof.NAMED = re.compile(r"^\s+(\S+\.[cm]?[jt]sx?)(?::\d+|: lines )", re.M)
proof.plant = plant
run.prepare = tsrun.prepare

if __name__ == "__main__":
    proof.main(*sys.argv[1:])

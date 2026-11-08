#!/usr/bin/env python3
"""mutscore — mutation scores per arm from mutate.py's results, with a bootstrap interval.

  mutscore.py <work dir> …

Per work dir, checkpoint, mutant set (written or control) and test set: each tree's score
(killed over killed and survived), each arm's pooled score, and cleat minus plain with a 95%
interval from a two-level bootstrap: the arm's trees drawn with replacement, then each drawn
tree's mutants. With two trees an arm, the tree level is coarse; it is there so that a gap no
wider than the spread between two trees of one arm does not read as an effect.
"""

import collections
import json
import os
import random
import sys

DRAWS = 2000


def rows(path):
    out = [json.loads(line)["status"] for line in open(path)]
    return [s == "killed" for s in out if s not in ("invalid", "compiler")]   # compiler: caught with no test


def score(kills):
    return 100.0 * sum(kills) / len(kills) if kills else None


def pooled(trees):
    return score([k for t in trees for k in t])


def draw(trees, rng):
    picked = [trees[rng.randrange(len(trees))] for _ in trees]
    return pooled([[t[rng.randrange(len(t))] for _ in t] for t in picked])


def interval(plain, cleat, rng):
    gaps = sorted(draw(cleat, rng) - draw(plain, rng) for _ in range(DRAWS))
    return gaps[int(0.025 * DRAWS)], gaps[int(0.975 * DRAWS)]


def report(work):
    base = os.path.join(work, "mutants")
    for point in sorted(os.listdir(base), key=int) if os.path.isdir(base) else []:
        groups = collections.defaultdict(dict)
        for name in sorted(os.listdir(os.path.join(base, point))):
            if not name.endswith(".jsonl"):
                continue
            stem = name[:-len(".jsonl")]
            kind = next((k for k in ("control", "types") if stem.endswith("-" + k)), "written")
            tree, tests = (stem[:-len(kind) - 1] if kind != "written" else stem).rsplit("-", 1)
            kills = rows(os.path.join(base, point, name))
            if kills:   # a set with nothing scored (every mutant invalid) says nothing
                groups[kind, tests][tree] = kills
        for (kind, tests), trees in sorted(groups.items()):
            arm = lambda a: [k for t, k in sorted(trees.items()) if t.split("-")[0] == a]
            plain, cleat = arm("plain"), arm("cleat")
            each = "  ".join("%s %.1f" % (t, score(k)) for t, k in sorted(trees.items()))
            line = "%s @%s %-7s %-8s plain %.1f  cleat %.1f" % (os.path.basename(work), point, kind, tests, pooled(plain), pooled(cleat))
            if plain and cleat:
                lo, hi = interval(plain, cleat, random.Random(0))
                line += "  gap %+.1f [%+.1f, %+.1f]" % (pooled(cleat) - pooled(plain), lo, hi)
            print(line + "   (" + each + ")")


def main(*works):
    for work in works:
        report(os.path.abspath(work))


if __name__ == "__main__":
    main(*sys.argv[1:])

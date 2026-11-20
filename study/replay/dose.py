#!/usr/bin/env python3
"""dose — does a change cost more where the code it touches is more complex?

  dose.py <out.json> <replay work dir> [<replay work dir> …]

Reads probe runs that probe.py already made; starts no agent. For each
probes/<point>/<tree>/<run>/repo: `git diff HEAD` on production files, the HEAD copy of each
changed file measured with cleat's own reader, each changed old line mapped to its function.
One row per run goes to <out.json>. Then, within each probe (the same change asked of every
tree, so the task is held fixed): how a run's cost and turns follow the highest cyclomatic
complexity among the functions it changed, how many of them were over 8, and the longest of
them, with a bootstrap interval over probes; and the cost of runs that touched a function
over 8 against runs of the same probe that did not.
"""
import collections
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile

CLEAT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(CLEAT, "quality", "bin"))
sys.path.insert(0, os.path.join(CLEAT, "study", "replay"))
sys.path.insert(0, os.path.join(CLEAT, "study", "paired"))
from extractors import complexity   # noqa: E402
import lang as languages            # noqa: E402

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
LANGS = {"ts": ["typescript", "javascript"], "typescript": ["typescript", "javascript"], "rust": ["rust"], "python": ["python"]}


def git(root, *args):
    return subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True, errors="replace").stdout


def hunks(root):
    """{file: [(old start, old count)]} for the uncommitted change, files that existed at HEAD only."""
    out, name = {}, None
    for line in git(root, "diff", "HEAD", "--unified=0", "--no-color").splitlines():
        if line.startswith("--- "):
            name = None if line.endswith("/dev/null") else line[6:]
        elif line.startswith("@@") and name:
            m = HUNK.match(line)
            start, count = int(m.group(1)), int(m.group(2) or 1)
            out.setdefault(name, []).append((start, count))
    return out


def added_lines(root):
    return sum(1 for line in git(root, "diff", "HEAD", "--unified=0", "--no-color").splitlines()
               if line.startswith("+") and not line.startswith("+++"))


def functions(root, files, lang):
    """{file: [(start, end, cc, length)]} for the HEAD copies of `files`."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        for f in files:
            dest = os.path.join(tmp, f)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w") as handle:
                handle.write(git(root, "show", "HEAD:" + f))
        got, _, _, _ = complexity.measure({"languages": LANGS[lang]}, [tmp], root=tmp)
        out = {}
        for fn in got:
            out.setdefault(os.path.relpath(os.path.realpath(fn.path), tmp), []).append((fn.line, fn.end, fn.cc, fn.length))
        return out


def one(run, lang):
    root = os.path.join(run, "repo")
    score = json.load(open(os.path.join(run, "score.json")))
    changed = {f: h for f, h in hunks(root).items() if lang.production(f) and not lang.is_test(f)}
    fns = functions(root, list(changed), lang.name) if changed else {}
    touched = set()
    for f, hs in changed.items():
        for start, count in hs:
            lo, hi = (start, start + count - 1) if count else (start, start + 1)   # a pure insertion sits between two lines
            for fn in fns.get(f, []):
                if fn[0] <= hi and lo <= fn[1]:
                    touched.add((f,) + fn)
    file_fns = [fn for f in changed for fn in fns.get(f, [])]
    ccs = [t[3] for t in touched]
    return dict(cost=score["run"].get("total_cost_usd"), turns=score["run"].get("num_turns"),
                resolved=score["judged"]["resolved"], mode=score.get("mode"),
                files=len(changed), touched=len(touched), max_cc=max(ccs, default=0), sum_cc=sum(ccs),
                over8=sum(c > 8 for c in ccs), max_len=max((t[4] for t in touched), default=0),
                file_max_cc=max((fn[2] for fn in file_fns), default=0),
                file_over8=sum(fn[2] > 8 for fn in file_fns), file_fns=len(file_fns), added=added_lines(root))


def correlation(a, b):
    mean_a, mean_b = statistics.mean(a), statistics.mean(b)
    da, db = [x - mean_a for x in a], [y - mean_b for y in b]
    spread = math.sqrt(sum(x * x for x in da) * sum(y * y for y in db))
    return sum(x * y for x, y in zip(da, db)) / spread if spread else float("nan")


def within(rows, x, y):
    """(correlation, slope) of y on x with each probe's own mean taken out of both."""
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row["task"]].append(row)
    xs, ys = [], []
    for group in groups.values():
        if len(group) > 1:
            mean_x, mean_y = statistics.mean(x(r) for r in group), statistics.mean(y(r) for r in group)
            xs += [x(r) - mean_x for r in group]
            ys += [y(r) - mean_y for r in group]
    squares = sum(v * v for v in xs)
    return correlation(xs, ys) if xs else float("nan"), (sum(a * b for a, b in zip(xs, ys)) / squares if squares else float("nan"))


def interval(rows, x, y, draws=1000):
    """The slope's 95% interval, resampling probes."""
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row["task"]].append(row)
    names, slopes = list(groups), []
    random.seed(1)
    for _ in range(draws):
        sample = [dict(r, task=str(i)) for i, name in enumerate(random.choices(names, k=len(names))) for r in groups[name]]
        slopes.append(within(sample, x, y)[1])
    slopes.sort()
    return slopes[int(draws * 0.025)], slopes[int(draws * 0.975)]


def report(rows):
    rows = [r for r in rows if r["cost"] and r["tree"] != "upstream"]
    log_cost = lambda r: math.log(r["cost"])   # noqa: E731
    print("%d runs over %d probes" % (len(rows), len({r["task"] for r in rows})))
    for name, x in (("highest cc touched", lambda r: r["max_cc"]), ("functions over 8 touched", lambda r: r["over8"]),
                    ("log longest touched", lambda r: math.log(1 + r["max_len"])), ("lines added", lambda r: r["added"])):
        r, slope = within(rows, x, log_cost)
        low, high = interval(rows, x, log_cost)
        print("  %-26s r %+.3f  log cost per unit %+.4f [%+.4f, %+.4f]  turns r %+.3f" % (name, r, slope, low, high, within(rows, x, lambda r: r["turns"])[0]))
    groups, ratios = collections.defaultdict(list), []
    for row in rows:
        groups[row["task"]].append(row)
    for group in groups.values():
        over, under = [r["cost"] for r in group if r["over8"]], [r["cost"] for r in group if not r["over8"]]
        if len(over) > 1 and len(under) > 1:
            ratios.append(statistics.median(over) / statistics.median(under))
    if ratios:
        print("probes with both kinds of run: %d; median cost, touched a function over 8 against not: %.2f times" % (len(ratios), statistics.median(ratios)))


def main(out, *works):
    rows = []
    for work in works:
        plan = json.load(open(os.path.join(work, "plan.json")))
        lang = languages.get(plan["lang"])
        base = os.path.join(work, "probes")
        for point in sorted(os.listdir(base)):
            for tree in sorted(os.listdir(os.path.join(base, point))):
                for name in sorted(os.listdir(os.path.join(base, point, tree))):
                    run = os.path.join(base, point, tree, name)
                    if not os.path.isfile(os.path.join(run, "score.json")):
                        continue
                    row = one(run, lang)
                    row.update(project=os.path.basename(work), point=int(point), tree=tree, run=name,
                               task="%s@%s/%s%s" % (os.path.basename(work), point, name.split("-")[0],
                                                    "-visible" if name.endswith("-visible") else ""))
                    rows.append(row)
        print(work, len(rows), flush=True)
    json.dump(rows, open(out, "w"), indent=1)
    report(rows)


if __name__ == "__main__":
    main(*sys.argv[1:])

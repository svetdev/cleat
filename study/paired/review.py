#!/usr/bin/env python3
"""review — the blind review of the paired diffs, pre-registered on 2026-11-02.

  review.py <work dir> [jobs] [pair id …]

REVIEW_ARMS names the two arms compared, the second the one whose share is reported
(default "plain,cleat"; any other pair writes to review-<x>-<y>/).

Each pair is a task and a repeat: its run without cleat against its run with it.
The reviewer sees the task text and the two production diffs as A and B, cleat's
files left out, and judges twice, the order drawn by seed and then swapped. A
pair's verdict is the side preferred in both orders, else a tie. Judgments go to
<work>/review/<pair>-<order>.json; a judgment already there is not asked again.
"""

import concurrent.futures
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run     # noqa: E402
import score   # noqa: E402

SEED = 20261102
X, Y = os.environ.get("REVIEW_ARMS", "plain,cleat").split(",")
TAG = "" if (X, Y) == ("plain", "cleat") else "-%s-%s" % (X, Y)
BUDGET = "1"
LEAK_RE = re.compile(r"\bcleat\b|quality gate|ratchet|baseline|cyclomatic|lizard", re.I)
SCORES = ("correctness", "readability", "maintainability")
SCHEMA = {"type": "object", "required": ["preferred", "A", "B", "reason"], "properties": {
    "preferred": {"enum": ["A", "B", "tie"]},
    "A": {"type": "object", "required": list(SCORES), "properties": {s: {"type": "integer", "minimum": 1, "maximum": 5} for s in SCORES}},
    "B": {"type": "object", "required": list(SCORES), "properties": {s: {"type": "integer", "minimum": 1, "maximum": 5} for s in SCORES}},
    "reason": {"type": "string"}}}
PROMPT = """You maintain the open-source Python project {repo}. Two contributors each sent a change for the task below. You cannot run them; read them as you would in review.

Which would you rather merge? Weigh, in this order: whether it does what the task asks and is correct, including edge cases; whether it is easy to read; whether it will be easy to maintain and fits the project's existing style. Prefer "tie" only when you would be genuinely indifferent. Score each change from 1 to 5 on correctness, readability and maintainability, and give your reason in a few sentences.

--- task ---
{text}

--- change A ---
{a}

--- change B ---
{b}
"""


def diff(root):
    """The run's production diff from where the agent started, new files included."""
    start = score.start_commit(root)
    parts = []
    for rel in score.changed_files(root, start):
        if not score.mine.production(rel):
            continue
        if subprocess.run(["git", "-C", root, "cat-file", "-e", "%s:%s" % (start, rel)], capture_output=True).returncode == 0:
            parts.append(score.out(root, "diff", start, "--", rel))
        elif os.path.isfile(os.path.join(root, rel)):
            parts.append(subprocess.run(["git", "-C", root, "diff", "--no-index", "/dev/null", rel],
                                        capture_output=True, text=True).stdout)
    return "".join(parts).strip() or "(no change to production code)"


def pairs(work):
    tasks = {t["id"]: t for t in json.load(open(os.path.join(work, "tasks.json")))["tasks"]}
    out = []
    for tid in sorted(tasks):
        for k in ("1", "2"):
            runs = {arm: os.path.join(work, "runs", tid, "%s-%s" % (arm, k), "repo") for arm in (X, Y)}
            if all(os.path.isdir(r) for r in runs.values()):
                out.append(dict(id="%s-%s" % (tid, k), task=tasks[tid], runs=runs))
    return out


def orders(pair_ids):
    """For each pair, which arm is A in its first judgment; the second swaps them."""
    rng = random.Random(SEED)
    return {p: rng.choice((X, Y)) for p in sorted(pair_ids)}


def judge(work, pair, first_a, order):
    path = os.path.join(work, "review" + TAG, "%s-%d.json" % (pair["id"], order))
    if os.path.isfile(path):
        return json.load(open(path))
    a_arm = first_a if order == 1 else (Y if first_a == X else X)
    b_arm = Y if a_arm == X else X
    prompt = PROMPT.format(repo=pair["task"]["repo"].replace("__", "/"), text=pair["task"]["text"],
                           a=pair["diffs"][a_arm], b=pair["diffs"][b_arm])
    with tempfile.TemporaryDirectory() as empty:
        p = subprocess.run(["claude", "-p", prompt, "--model", run.MODEL, "--tools", "", "--setting-sources", "project",
                            "--strict-mcp-config", "--json-schema", json.dumps(SCHEMA), "--output-format", "json",
                            "--max-budget-usd", BUDGET, "--no-session-persistence"],
                           cwd=empty, capture_output=True, text=True, timeout=900, env=run.clean_env())
    result = json.loads(p.stdout) if p.stdout.strip().startswith("{") else {}
    verdict = result.get("structured_output")
    if not verdict:
        raise RuntimeError("%s order %d: no verdict (exit %s): %s" % (pair["id"], order, p.returncode, (p.stdout + p.stderr)[-500:]))
    record = dict(pair=pair["id"], order=order, A=a_arm, B=b_arm, verdict=verdict,
                  preferred=verdict["preferred"] if verdict["preferred"] == "tie" else {"A": a_arm, "B": b_arm}[verdict["preferred"]],
                  cost=result.get("total_cost_usd"))
    with open(path, "w") as handle:
        json.dump(record, handle, indent=1)
    return record


def sign_test(wins, losses):
    """Two-sided exact binomial p of `wins` against even."""
    n = wins + losses
    if not n:
        return 1.0
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2.0 ** n
    return min(1.0, 2 * tail)


def resolved(work, pair_id, arm):
    tid, k = pair_id.rsplit("-", 1)
    return json.load(open(os.path.join(work, "runs", tid, "%s-%s" % (arm, k), "score.json")))["score"]["resolved"]


def summarize(work, done):
    by_pair = {}
    for r in done:
        by_pair.setdefault(r["pair"], []).append(r)
    verdicts = {}
    for pid, (one, two) in ((p, sorted(v, key=lambda r: r["order"])) for p, v in by_pair.items() if len(v) == 2):
        verdicts[pid] = one["preferred"] if one["preferred"] == two["preferred"] else "tie"
    def share(ids):
        c = sum(verdicts[i] == Y for i in ids)
        p = sum(verdicts[i] == X for i in ids)
        return c, p, len(ids) - c - p, (c / float(c + p) if c + p else float("nan")), sign_test(c, p)
    both = [i for i in verdicts if all(resolved(work, i, arm) for arm in (X, Y))]
    first = sum(r["preferred"] == r["A"] for r in done if r["preferred"] != "tie")
    decided = sum(r["preferred"] != "tie" for r in done)
    print("judgments %d, pairs judged both ways %d; position A preferred in %d of %d decided judgments" % (len(done), len(verdicts), first, decided))
    checks = []
    for name, ids in (("all pairs", list(verdicts)), ("pairs both runs resolved", both)):
        c, p, t, s, pv = share(ids)
        held = 0.35 <= s <= 0.65 and pv >= 0.05
        checks.append({"name": name, Y: c, X: p, "tie": t, "share": s, "p": pv, "even": held})
        print("  %-26s %s %2d, %s %2d, tie %2d — %s's share %.2f, sign test p %.3f  %s" % (
            name, Y, c, X, p, t, Y, s, pv, "even" if held else "NOT EVEN"))
    means = {arm: {s: statistics.mean(r["verdict"]["A" if r["A"] == arm else "B"][s] for r in done) for s in SCORES} for arm in (X, Y)}
    for arm in (X, Y):
        print("  mean scores %-5s %s" % (arm, ", ".join("%s %.2f" % (s, means[arm][s]) for s in SCORES)))
    print("  reviewer usage $%.2f" % sum(r.get("cost") or 0 for r in done))
    with open(os.path.join(work, "review-summary%s.json" % TAG), "w") as handle:
        json.dump(dict(verdicts=verdicts, checks=checks, means=means, position_a=[first, decided]), handle, indent=1)


def main(work, jobs="4", *ids):
    work = os.path.abspath(work)
    os.makedirs(os.path.join(work, "review" + TAG), exist_ok=True)
    todo = [p for p in pairs(work) if not ids or p["id"] in ids]
    leaks = []
    for p in todo:
        p["diffs"] = {arm: diff(root) for arm, root in p["runs"].items()}
        leaks += ["%s %s: %s" % (p["id"], arm, line.strip()[:100]) for arm, d in p["diffs"].items()
                  for line in d.splitlines() if line.startswith("+") and LEAK_RE.search(line)]
    if leaks:
        print("lines that could name the arm — check before reviewing:\n  " + "\n  ".join(leaks))
        if "--allow-leaks" not in os.environ.get("REVIEW_FLAGS", ""):
            sys.exit(1)
    first = orders(p["id"] for p in pairs(work))
    jobs_list = [(p, first[p["id"]], o) for p in todo for o in (1, 2)]
    with concurrent.futures.ThreadPoolExecutor(int(jobs)) as pool:
        done = list(pool.map(lambda j: judge(work, *j), jobs_list))
    summarize(work, done)


if __name__ == "__main__":
    main(*sys.argv[1:])

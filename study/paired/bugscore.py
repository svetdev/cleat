#!/usr/bin/env python3
"""bugscore — did each run of a bugdraw.py task repeat the bug the merged commit carried?

  bugscore.py <clones dir> <work dir> [arm …]

After run.py has run and scored the tasks. Per run: the fix's test files are laid over the
agent's tree and the bug tests run (the tests that failed on the merged commit and passed
with the fix). The run avoided the bug when every one passes. Written beside the run's
score.json as bug.json, so a second call only reports.

Printed per arm, for all tasks and for the tasks whose commit the gates would have refused:
runs, runs resolved by the commit's own tests, and runs that avoided the bug, among all and
among the resolved. A run that did not resolve the task fails the bug tests for that reason,
so the resolved ones are the comparison. Then the tasks where the arms differ.
"""

import collections
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import draw   # noqa: E402


def judge(clones, task, directory):
    path = os.path.join(directory, "bug.json")
    if os.path.isfile(path):
        return json.load(open(path))
    root, bug = os.path.join(directory, "repo"), task["bug"]
    draw.overlay(os.path.join(clones, task["repo"]), bug["sha"], root, bug["overlay"])
    outcome = draw.pytest(root, bug["tests"], "bug")
    passed = [t for t in bug["f2p"] if outcome.get(t)]
    result = dict(avoided=len(passed) == len(bug["f2p"]), passed=len(passed), of=len(bug["f2p"]), ran=len(outcome))
    with open(path, "w") as handle:
        json.dump(result, handle)
    return result


def line(label, rows):
    done = [r for r in rows if r["resolved"]]
    print("    %-8s %3d runs | resolved %3d | avoided the bug %3d of all, %3d of the %d resolved" % (
        label, len(rows), len(done), sum(r["avoided"] for r in rows), sum(r["avoided"] for r in done), len(done)))


def main(clones, work, *arms):
    arms = arms or ("plain", "cleat")
    tasks = json.load(open(os.path.join(work, "tasks.json")))["tasks"]
    rows = []
    for task in tasks:
        for directory in sorted(glob.glob(os.path.join(work, "runs", task["id"], "*"))):
            if os.path.isfile(os.path.join(directory, "score.json")):
                score = json.load(open(os.path.join(directory, "score.json")))["score"]
                rows.append(dict(judge(os.path.abspath(clones), task, directory), task=task["id"], refused=bool(task["refused"]),
                                 arm=os.path.basename(directory).split("-")[0], resolved=bool(score.get("resolved")),
                                 gate=bool(score["real_gate"]["failing"]), cost=score["run"].get("total_cost_usd") or 0))
    for label, part in (("all tasks", rows), ("tasks the gates would have refused", [r for r in rows if r["refused"]]),
                        ("tasks the gates would have passed", [r for r in rows if not r["refused"]])):
        print("%s (%d)" % (label, len({r["task"] for r in part})))
        for arm in arms:
            line(arm, [r for r in part if r["arm"] == arm])
    by_task = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        by_task[r["task"]][r["arm"]].append(r)
    print("per task: resolved and avoided, of the runs")
    for task, mine in sorted(by_task.items()):
        print("  %-44s %s  %s" % (task, "refused" if mine[arms[0]] and mine[arms[0]][0]["refused"] else "passed ", "  ".join(
            "%s %d/%d/%d" % (arm, sum(r["resolved"] for r in mine[arm]), sum(r["avoided"] for r in mine[arm]), len(mine[arm])) for arm in arms)))
    print("runs ending with a gate failing: " + ", ".join("%s %d" % (arm, sum(r["gate"] for r in rows if r["arm"] == arm)) for arm in arms)
          + "; spent $%.2f" % sum(r["cost"] for r in rows))


if __name__ == "__main__":
    main(*sys.argv[1:])

#!/usr/bin/env python3
"""proof — before any agent runs, show that each arm's gates judge these tasks correctly.

  proof.py <clones dir> <work dir> [jobs] [task id …]        (arms from STUDY_ARMS, as run.py)

Starts no agent. A gate put in front of an agent unproven has twice judged garbage here: a
coverage report from a suite that never ran, and test functions read as untested production
code. Per task and arm, on the tree run.py would hand the agent:

  inputs   attach is green on day one; where the arm reads coverage, the suite ran (pytest
           exit 0 or 1), SHARE of its tests pass, and a CRAP baseline was written.
  clean    the gates pass on the untouched tree.
  plant    a violation of the arm's own gate is put in a production file the commit changes
           and must be refused by that gate: an untested branching function for a coverage
           or CRAP arm, a function of cyclomatic 12 for the others. For a public-API arm a public
           function is renamed, which must be refused, and one added, which must not be.
  paid     for a coverage or CRAP arm, the same function with a test that walks every branch
           of it must pass: a gate that reads every function as uncovered refuses this too.
  bare     the real commit's production change is laid on without its tests and the gates run:
           where the arm's own gate does not refuse that, it cannot fire on the task.
  oracle   the real merged commit is laid on, tests and all, and the gates run. A person
           merged it, so a refusal is read by eye. One that names a test file is a defect in
           the gate and fails the proof; one that names production code is debt upstream
           took, kept and counted: it is also where the gate can fire.

Writes <work>/proof/<task>/<arm>.json and <work>/proof.json: per task, whether every arm is
proven and which gates refused the real commit. run.py's tasks should be the proven ones.
"""

import concurrent.futures
import json
import os
import re
import shutil
import signal
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import draw   # noqa: E402
import run    # noqa: E402

SHARE, LIMIT = 0.9, 900
TEST = re.compile(r"(^|/)(tests?|t|testing)/|(^|/)test_|_test\.py$|conftest")
NAMED = re.compile(r"^\s+(\S+\.py)(?::\d+|: lines )", re.M)
FAILED = re.compile(r"^\s+FAIL\s+(\S+)", re.M)
UNTESTED = '''

def proof_pick_kind(value, strict, fallback):
    if value is None:
        return fallback
    if isinstance(value, str) and strict:
        return "text"
    if isinstance(value, (int, float)):
        return "number" if value >= 0 else "negative"
    return fallback
'''
PAYS = """from %s import proof_pick_kind


def test_proof_pick_kind():
    assert proof_pick_kind(None, True, "x") == "x"
    assert proof_pick_kind("a", True, "x") == "text"
    assert proof_pick_kind("a", False, "x") == "x"
    assert proof_pick_kind(1, True, "x") == "number"
    assert proof_pick_kind(-1.5, True, "x") == "negative"
"""
TANGLED = "\n\ndef proof_tangled(a, b, c):\n" + "".join("    if a == %d and b:\n        c += %d\n" % (n, n) for n in range(12)) + "    return c\n"


def gates(root):
    """(exit, output, [failing gate]) of the gates as the Stop hook scopes them, without its once-only memory."""
    process = subprocess.Popen(["python3", "quality/bin/gate.py", "--changed"], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, env=run.clean_env(), start_new_session=True)
    try:
        said, _ = process.communicate(timeout=LIMIT)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        return 124, "the gates took over %ds" % LIMIT, ["timeout"]
    return process.returncode, said, FAILED.findall(said)


def restore(root):
    run.git(root, "checkout", "-q", "--", ".")
    run.git(root, "clean", "-fdq")


def inputs(info):
    for key in ("coverage", "crap"):
        suite = (info.get(key) or {}).get("suite")
        if suite is None:
            continue
        if "timeout" in suite or suite["exit"] > 1:
            return "the suite does not run under coverage: %s" % suite
        counts = [int(n) for n, _ in re.findall(r"(\d+) (passed|failed|errors?)", suite["said"])]
        passed = sum(int(n) for n, word in re.findall(r"(\d+) (passed|failed|errors?)", suite["said"]) if word == "passed")
        if passed < SHARE * sum(counts):
            return "under %d%% of the suite passes: %s" % (100 * SHARE, suite["said"])
    for key in ("crap", "api"):
        if key in info and info[key].get("baseline") != 0:
            return "no %s baseline: %s" % (key, info[key])
    return None if info.get("day_one_gate", 0) == 0 else "attach is red on day one"


PUBLIC = re.compile(r"^(def|class) ([a-z]\w*)", re.M)


def shipped(root):
    """The Python files of the project's packages, tests and private modules left out."""
    found = []
    for top in run.packages(root):
        for directory, _, names in os.walk(os.path.join(root, top)):
            if not set(directory.split(os.sep)) & run.NOT_SHIPPED:
                found += [os.path.join(directory, n) for n in names if n.endswith(".py") and not n.startswith("_")]
    return sorted(found)


def plant_api(root):
    """(what must refuse, problem or None) for a public-API arm: a public function or class
    renamed in the first shipped file that has one must be refused, and one added must not."""
    target = next((f for f in shipped(root) if PUBLIC.search(open(f, errors="replace").read())), None)
    if target is None:
        return ["public-api"], "no shipped file declares a public function or class"
    text = open(target, errors="replace").read()
    for changed, must_fail in ((PUBLIC.sub(r"\1 \2_proof_renamed", text, count=1), True), (text + "\n\ndef proof_added():\n    return 1\n", False)):
        with open(target, "w") as handle:
            handle.write(changed)
        _, said, failing = gates(root)
        restore(root)
        if ("public-api" in failing) != must_fail:
            return ["public-api"], "a public signature %s was %s: %s" % ("renamed" if must_fail else "added", "not refused" if must_fail else "refused", said[-300:])
    return ["public-api"], None


def plant(task, arm, root):
    """(what must refuse, problem or None): the arm's own violation, refused by its own gate."""
    name = arm.split("-")[0]
    if name in run.API_ARMS:
        return plant_api(root)
    reads_coverage = name in run.COVERAGE_ARMS | run.CRAP_ARMS
    target = next((f[2] for f in task["files"] if f[0] == "M" and f[2].endswith(".py") and not TEST.search(f[2])
                   and os.path.isfile(os.path.join(root, f[2]))), None)
    if target is None:
        return None, "the commit changes no production file that exists at its parent"
    with open(os.path.join(root, target), "a") as handle:
        handle.write(UNTESTED if reads_coverage else TANGLED)
    wanted = [g for g, arms in (("crap", run.CRAP_ARMS), ("changed-coverage", run.COVERAGE_ARMS)) if name in arms] or ["complexity"]
    _, said, failing = gates(root)
    restore(root)
    missing = [g for g in wanted if g not in failing]
    if missing or not reads_coverage:
        return wanted, ("the planted violation was not refused by %s: %s" % (", ".join(missing), said[-300:]) if missing else None)
    with open(os.path.join(root, target), "a") as handle:
        handle.write(UNTESTED)
    module = re.sub(r"^src/", "", target)[:-3].replace("/", ".")
    with open(os.path.join(root, os.path.dirname(task["tests"][0]), "test_proof_paid.py"), "w") as handle:
        handle.write(PAYS % module)
    _, said, failing = gates(root)
    restore(root)
    still = [g for g in wanted if g in failing]
    return wanted, ("the same function with every branch tested is still refused by %s: %s" % (", ".join(still), said[-400:]) if still else None)


def bare(clones, task, root):
    """The gates that refuse the real commit's production change laid on without its tests: where
    the arm's gate can fire on this task at all. Nothing is failed by it; it is read."""
    draw.overlay(os.path.join(clones, task["repo"]), task["sha"], root, [f[2] for f in task["files"] if not TEST.search(f[2])])
    _, _, failing = gates(root)
    restore(root)
    return failing


def one(clones, work, task, arm):
    kept = os.path.join(work, "proof", task["id"], arm + ".json")
    if os.path.isfile(kept):
        return json.load(open(kept))
    root = os.path.join(work, "proof", task["id"], arm, "repo")
    shutil.rmtree(os.path.dirname(root), ignore_errors=True)
    os.makedirs(os.path.dirname(root))
    row = dict(task=task["id"], arm=arm, proven=False, oracle=[], oracle_tests=[])
    try:
        row["why"] = inputs(run.prepare(clones, task, arm, root))
        if not row["why"]:
            code, said, failing = gates(root)
            row["why"] = "the gates fail on the untouched tree: %s %s" % (failing, said[-300:]) if code else None
        if not row["why"]:
            row["planted"], row["why"] = plant(task, arm, root)
        if not row["why"]:
            row["bare"] = bare(clones, task, root)
        if not row["why"]:
            draw.overlay(os.path.join(clones, task["repo"]), task["sha"], root, [f[2] for f in task["files"]])
            _, said, row["oracle"] = gates(root)
            row["oracle_said"] = said[-1500:] if row["oracle"] else ""
            row["oracle_tests"] = sorted({n for n in NAMED.findall(said) if TEST.search(n)}) if row["oracle"] else []
            judged = row["oracle_tests"] and not run.STRIP_TESTS   # a test file put back whole is all new: its old skips are not the gate's fault
            row["why"] = "a gate refuses the real commit naming a test file: %s" % row["oracle_tests"] if judged else None
        row["proven"] = not row["why"]
    except Exception as problem:   # one broken tree must not lose the others' proofs
        row["why"] = "error: " + repr(problem)[:300]
    shutil.rmtree(os.path.dirname(root), ignore_errors=True)
    with open(kept, "w") as handle:
        json.dump(row, handle, indent=1)
    return row


def main(clones, work, jobs="4", *ids):
    clones, work = os.path.abspath(clones), os.path.abspath(work)
    tasks = [t for t in json.load(open(os.path.join(work, "tasks.json")))["tasks"] if not ids or t["id"] in ids]
    pairs = [(t, arm) for t in tasks for arm in run.ARMS]
    with concurrent.futures.ThreadPoolExecutor(int(jobs)) as pool:
        rows = list(pool.map(lambda pair: one(clones, work, *pair), pairs))
    out = {}
    for task in tasks:
        mine = [r for r in rows if r["task"] == task["id"]]
        out[task["id"]] = dict(proven=all(r["proven"] for r in mine), oracle={r["arm"]: r["oracle"] for r in mine},
                               why={r["arm"]: r["why"] for r in mine if r["why"]})
        print("%-46s %s  real commit refused by: %s  %s" % (task["id"], "proven  " if out[task["id"]]["proven"] else "UNPROVEN",
              {a: g for a, g in out[task["id"]]["oracle"].items() if g} or "nothing", "; ".join("%s: %s" % (a, w[:110]) for a, w in out[task["id"]]["why"].items())), flush=True)
    with open(os.path.join(work, "proof.json"), "w") as handle:
        json.dump(out, handle, indent=1)
    print("%d of %d tasks proven in every arm (%s)" % (sum(v["proven"] for v in out.values()), len(out), ", ".join(run.ARMS)))


if __name__ == "__main__":
    main(*sys.argv[1:])

#!/usr/bin/env python3
"""run — each drawn task once without cleat and once with it, then score both.

  run.py <clones dir> <work dir> [jobs] [repeats] [task id …]

Per task and arm: the parent's tree with no history (one "base" commit), the
project's test dependencies in .venv, and for the cleat arm `attach.py --into`
committed on top. The agent is `claude -p` with the same prompt, model and flags
in both arms (deny by default, an allowlist, Bash sandboxed); only project settings load, so the cleat arm's hooks are the ones
attach wrote and the other arm has none. Scoring is score.py's.
"""

import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
CLEAT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import draw    # noqa: E402
import score   # noqa: E402

MODEL = os.environ.get("STUDY_MODEL", "claude-opus-5-5")   # another agent model, for probes with a weaker one
BUDGET = "5"
TIMEOUT = 2700
# The arms, and the cleat checkout each attaches: STUDY_ARMS names them (default plain and
# cleat), CLEAT_<ARM> overrides an arm's checkout — a variant of cleat runs as its own arm.
ARMS = os.environ.get("STUDY_ARMS", "plain,cleat").split(",")
CHECKOUTS = {arm: os.environ.get("CLEAT_" + arm.upper(), CLEAT) for arm in ARMS if arm != "plain"}
# Arms that keep what attach wrote but run with no hooks: the instructions alone, nothing enforced.
NO_HOOKS = set(filter(None, os.environ.get("STUDY_NO_HOOKS", "").split(",")))
# Arms whose complexity ceiling differs from attach's: STUDY_CEILINGS="loose=12" sets cyclomatic 12
# after attach, rewrites the complexity baseline at it, and has the agent's block name it.
CEILINGS = dict((a, int(n)) for a, n in (x.split("=") for x in filter(None, os.environ.get("STUDY_CEILINGS", "").split(","))))
# Arms attached with some gates taken out: STUDY_DROP_GATES="lean=complexity+duplication" deletes
# those sections from quality.json after attach, so the gate and the Stop hook never run them.
DROP_GATES = dict((a, g.split("+")) for a, g in (x.split("=") for x in filter(None, os.environ.get("STUDY_DROP_GATES", "").split(","))))
# Arms that get words and nothing else: no attach, no gates in the tree, no hooks.
WORDS_ONLY = set(filter(None, os.environ.get("STUDY_WORDS_ONLY", "").split(",")))
WORDS = """
## Functions

Write each function simple from the start: one job, shallow nesting, few
branches. Keep a function to a cyclomatic complexity of at most 8 and at most
60 lines; a helper only one function calls counts with its caller, so cutting
a function up afterwards does not make it simpler. Shape it simply before
writing it.
"""
# Arms that get no gates and are asked for more work instead: the control for cleat's extra turns.
EFFORT_ARMS = set(filter(None, os.environ.get("STUDY_EFFORT", "").split(",")))
EFFORT = """
## Before you stop

Do not stop at the first version that passes. Reread your whole change against
the task, run the full test suite, add tests for the behaviour you changed and
its edge cases, and rework anything you would not want to maintain. Then go
through the change once more before you stop.
"""
# Arms with changed-line coverage as a gate at every stop: STUDY_COVERAGE="cov". After attach,
# coverage goes into .venv and one `commands` entry runs the suite under it, writes the report and
# judges the changed lines. It is one entry because a section's gates run before `commands`, so a
# separate changed_coverage gate would read the report of the stop before. Tests are left out of the
# report: counted, the test lines an agent adds would all have run and carry any change over the minimum.
COVERAGE_ARMS = set(filter(None, os.environ.get("STUDY_COVERAGE", "").split(",")))
COVERAGE_OMIT = "*/tests/*,*/test/*,*/t/*,*/testing/*,*/test_*.py,*_test.py,*/conftest.py,tests/*,test/*,t/*,testing/*"
COVERAGE_RUN = ("rm -f .coverage quality/.coverage.xml; "
                ".venv/bin/python -m coverage run --omit='%s' -m pytest -q -p no:cacheprovider >/dev/null 2>&1; "
                ".venv/bin/python -m coverage xml -q -o quality/.coverage.xml >/dev/null 2>&1; "
                "python3 quality/bin/check-changed-coverage.py" % COVERAGE_OMIT)
COVERAGE_MIN_LINES = 5   # most of these tasks change fewer executable lines than the check's default of 20

PROMPT = """You are working in a Python repository. Implement the change described below.

A virtualenv with the project and its test dependencies installed is at .venv; run the tests with `.venv/bin/python -m pytest`. Do not use the network. When the change is done, stop.

--- task ---
{text}
"""


def clean_env():
    env = {k: v for k, v in os.environ.items() if not (k.startswith("CLAUDE") or k == "CLAUDECODE")}
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    return env


def git(root, *args):
    subprocess.run(["git", "-C", root, "-c", "user.name=study", "-c", "user.email=study@example.invalid", *args],
                   check=True, capture_output=True)


def drop_hooks(root):
    path = os.path.join(root, ".claude", "settings.json")
    with open(path) as handle:
        settings = json.load(handle)
    settings.pop("hooks", None)
    with open(path, "w") as handle:
        json.dump(settings, handle, indent=2)


def drop_gates(root, gates):
    path = os.path.join(root, "quality.json")
    with open(path) as handle:
        config = json.load(handle)
    gone = [g for g in gates if config.pop(g, None) is not None]
    with open(path, "w") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    return gone


def set_ceiling(root, cc):
    path = os.path.join(root, "quality.json")
    with open(path) as handle:
        config = json.load(handle)
    config["complexity"]["ceilings"]["cc"] = cc
    with open(path, "w") as handle:
        json.dump(config, handle, indent=2)
    for doc in ("CLAUDE.md", "AGENTS.md"):
        doc = os.path.join(root, doc)
        if os.path.isfile(doc):
            with open(doc) as handle:
                text = handle.read()
            with open(doc, "w") as handle:
                handle.write(text.replace("cyclomatic 8,", "cyclomatic %d," % cc))
    out = subprocess.run(["python3", "quality/bin/check-complexity.py", "--write-baseline"], cwd=root,
                         capture_output=True, text=True, env=clean_env())
    return dict(cc=cc, baseline=out.returncode, tail=(out.stdout + out.stderr)[-300:])


def add_coverage(root):
    venv_python = os.path.join(root, ".venv", "bin", "python")
    subprocess.run([venv_python, "-m", "pip", "install", "-q", "--disable-pip-version-check", "coverage"], check=True, capture_output=True)
    path = os.path.join(root, "quality.json")
    with open(path) as handle:
        config = json.load(handle)
    config["changed_coverage"] = {"report": "quality/.coverage.xml", "minimum": 0.8, "min_lines": COVERAGE_MIN_LINES}
    config.setdefault("commands", []).append({"name": "changed-coverage", "run": COVERAGE_RUN})
    with open(path, "w") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    with open(os.path.join(root, ".gitignore"), "a") as handle:
        handle.write("\n.coverage\n.coverage.*\nquality/.coverage.xml\n")
    return config["changed_coverage"]


def prepare(clones, task, arm, root):
    draw.tree(os.path.join(clones, task["repo"]), task["parent"], root)
    with open(os.path.join(root, ".gitignore"), "a") as handle:
        handle.write("\n.venv/\n")
    draw.install_recipe(root, task["recipe"])
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    info = {}
    if arm.split("-")[0] in WORDS_ONLY | EFFORT_ARMS:
        doc = next((d for d in ("CLAUDE.md", "AGENTS.md") if os.path.isfile(os.path.join(root, d))), "CLAUDE.md")
        with open(os.path.join(root, doc), "a") as handle:
            handle.write(EFFORT if arm.split("-")[0] in EFFORT_ARMS else WORDS)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "attach cleat")
        info["words"] = doc
    elif arm.split("-")[0] != "plain":
        checkout = CHECKOUTS[arm.split("-")[0]]
        out = subprocess.run(["python3", os.path.join(checkout, "quality", "bin", "attach.py"), "--into", root],
                             capture_output=True, text=True, env=clean_env())
        info["attach"] = out.stdout[-3000:] + out.stderr[-1000:]
        if arm.split("-")[0] in NO_HOOKS:
            drop_hooks(root)
        if arm.split("-")[0] in DROP_GATES:
            info["dropped"] = drop_gates(root, DROP_GATES[arm.split("-")[0]])
        if arm.split("-")[0] in COVERAGE_ARMS:
            info["coverage"] = add_coverage(root)
        if arm.split("-")[0] in CEILINGS:
            info["ceiling"] = set_ceiling(root, CEILINGS[arm.split("-")[0]])
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "attach cleat")
        gate = subprocess.run(["python3", "quality/bin/gate.py"], cwd=root, capture_output=True, text=True, env=clean_env())
        info["day_one_gate"] = gate.returncode
        info["hooks"] = sorted(json.load(open(os.path.join(root, ".claude", "settings.json"))).get("hooks", {}))
    subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], check=True, capture_output=True)
    return info


# Nothing is allowed that is not listed: file edits only under the task's tree, Bash only for the
# task's own Python and read-only inspection. Bash runs in the OS sandbox (writes confined to the
# tree, no network); the clones and the drawn tasks, which hold the real fix and its tests, are
# unreadable from it. File tools and hooks run outside the sandbox, so the allowlist bounds them.
ALLOWED = ["Read", "Glob", "Grep", "Edit(./**)", "Write(./**)", "Bash(.venv/bin/python *)",
           "Bash(git status *)", "Bash(git diff *)", "Bash(git log *)", "Bash(git show *)",
           "Bash(ls *)", "Bash(head *)", "Bash(tail *)", "Bash(wc *)",
           "Bash(python3 quality/bin/*)"]   # cleat's own gates, which its instructions tell the agent to run


def hidden(clones, work, root=None):
    """What the agent must not read: the clones and the draw, which hold the real fix, and
    every other run's tree — another arm's answer to the same task."""
    paths = [clones, os.path.join(work, "validate"), os.path.join(work, "tasks.json"), os.path.join(work, "tried.jsonl")]
    if root:
        own = os.path.dirname(os.path.abspath(root))
        runs = os.path.join(work, "runs")
        paths += [d for d in (os.path.join(runs, t, a) for t in os.listdir(runs) for a in os.listdir(os.path.join(runs, t)))
                  if os.path.abspath(d) != own]
    return paths


def sandbox(clones, work, root=None):
    # autoAllowBashIfSandboxed would approve any sandboxed command and make the allowlist moot;
    # failIfUnavailable refuses to run unsandboxed. The sandbox binds Bash only; the file tools
    # run outside it, so the same paths are denied to them as permission rules.
    paths = hidden(clones, work, root)
    deny = ["%s(/%s%s)" % (tool, p, "/**" if os.path.isdir(p) else "") for p in paths
            for tool in ("Read", "Grep", "Glob", "Edit", "Write")]
    return json.dumps({"permissions": {"deny": deny},
                       "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": False, "failIfUnavailable": True,
                                   "filesystem": {"denyRead": paths}}})


def agent(task, root, out_dir, clones, work):
    cmd = ["claude", "-p", PROMPT.format(text=task["text"]), "--model", MODEL, "--setting-sources", "project",
           "--settings", sandbox(clones, work, root), "--strict-mcp-config", "--permission-mode", "dontAsk",
           "--allowedTools", ",".join(ALLOWED), "--disallowedTools", "WebFetch,WebSearch,Agent",
           "--max-budget-usd", BUDGET, "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    with open(os.path.join(out_dir, "transcript.jsonl"), "w") as handle:
        try:
            p = subprocess.run(cmd, cwd=root, stdout=handle, stderr=subprocess.PIPE, text=True,
                               timeout=TIMEOUT, env=clean_env())
            return dict(exit=p.returncode, stderr=p.stderr[-2000:])
        except subprocess.TimeoutExpired:
            return dict(exit="timeout")


LIMIT_RE = re.compile(r"usage limit|hit your limit|limit reached|rate.?limit|overloaded", re.I)
PARKED = threading.Event()


def limited(out_dir, info):
    """Whether the run ended on the plan's usage limit rather than on the task: an errored or
    missing result naming a limit. Such a run is no outcome — it is left unscored to run again."""
    last = None
    with open(os.path.join(out_dir, "transcript.jsonl"), errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "result":
                last = event
    if last and not last.get("is_error"):
        return False
    text = json.dumps(last or {}) + info.get("stderr", "")
    return bool(LIMIT_RE.search(text))


def one(clones, work, task, arm):
    out_dir = os.path.join(work, "runs", task["id"], arm)
    if os.path.isfile(os.path.join(out_dir, "score.json")):
        return task["id"], arm, "cached"
    if PARKED.is_set():
        return task["id"], arm, "skipped: usage limit"
    root = os.path.join(out_dir, "repo")
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)
    info = prepare(clones, task, arm, root)
    info["agent"] = agent(task, root, out_dir, clones, work)
    if limited(out_dir, info["agent"]):
        PARKED.set()
        return task["id"], arm, "parked: usage limit, run again after it resets"
    info["score"] = score.score(clones, task, arm, root, out_dir)
    with open(os.path.join(out_dir, "score.json"), "w") as handle:
        json.dump(info, handle, indent=1)
    return task["id"], arm, "resolved=%s" % info["score"].get("resolved")


def main(clones, work, jobs="4", repeats="1", *ids):
    clones, work = os.path.abspath(clones), os.path.abspath(work)
    with open(os.path.join(work, "tasks.json")) as handle:
        tasks = json.load(handle)["tasks"]
    if ids:
        tasks = [t for t in tasks if t["id"] in ids]
    runs = ["%s-%d" % (arm, r) for r in range(1, int(repeats) + 1) for arm in ARMS] if int(repeats) > 1 else ARMS
    pairs = [(t, arm) for t in tasks for arm in runs]
    for t, arm in pairs:   # every run's directory exists before any agent starts, so each is denied the others
        os.makedirs(os.path.join(work, "runs", t["id"], arm), exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(int(jobs)) as pool:
        for result in pool.map(lambda p: one(clones, work, *p), pairs):
            print(*result, flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])

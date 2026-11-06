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

MODEL = "claude-opus-5-5"
BUDGET = "5"
TIMEOUT = 2700
# The arms, and the cleat checkout each attaches: STUDY_ARMS names them (default plain and
# cleat), CLEAT_<ARM> overrides an arm's checkout — a variant of cleat runs as its own arm.
ARMS = os.environ.get("STUDY_ARMS", "plain,cleat").split(",")
CHECKOUTS = {arm: os.environ.get("CLEAT_" + arm.upper(), CLEAT) for arm in ARMS if arm != "plain"}
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


def prepare(clones, task, arm, root):
    draw.tree(os.path.join(clones, task["repo"]), task["parent"], root)
    with open(os.path.join(root, ".gitignore"), "a") as handle:
        handle.write("\n.venv/\n")
    draw.install_recipe(root, task["recipe"])
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    info = {}
    if arm.split("-")[0] != "plain":
        checkout = CHECKOUTS[arm.split("-")[0]]
        out = subprocess.run(["python3", os.path.join(checkout, "quality", "bin", "attach.py"), "--into", root],
                             capture_output=True, text=True, env=clean_env())
        info["attach"] = out.stdout[-3000:] + out.stderr[-1000:]
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

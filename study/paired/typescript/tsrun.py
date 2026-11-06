#!/usr/bin/env python3
"""tsrun — run.py for the TypeScript tasks: each drawn task once per arm, then score it.

  tsrun.py <clones dir> <work dir> [jobs] [repeats] [task id …]
  tsrun.py --prepare-only <clones dir> <work dir> <task id> <arm>

The same arms (STUDY_ARMS, CLEAT_<ARM>), model, flags, sandbox and usage-limit parking
as run.py. What differs is the tree's preparation (node_modules by the repository's own
manager and lockfile instead of a venv), the prompt's line on running the tests, and the
allowlist: the repository's runners through its local binaries in place of
`.venv/bin/python *`. node_modules are removed once a run is scored; the sources stay
for review.
"""

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import run       # noqa: E402
import draw      # noqa: E402
import tsdraw    # noqa: E402
import tsscore   # noqa: E402

PROMPT = """You are working in a TypeScript repository. Implement the change described below.

The project's dependencies are installed in node_modules; run the tests with `{hint}`. Do not use the network. When the change is done, stop.

--- task ---
{text}
"""

ALLOWED = ["Read", "Glob", "Grep", "Edit(./**)", "Write(./**)",
           "Bash(npx vitest *)", "Bash(npx jest *)", "Bash(npx mocha *)", "Bash(npx tsc *)",
           "Bash(pnpm test *)", "Bash(pnpm run *)", "Bash(pnpm exec *)", "Bash(pnpm vitest *)",
           "Bash(yarn test *)", "Bash(yarn run *)", "Bash(npm test *)", "Bash(npm run *)",
           "Bash(node *)", "Bash(cd *)",
           "Bash(git status *)", "Bash(git diff *)", "Bash(git log *)", "Bash(git show *)",
           "Bash(ls *)", "Bash(head *)", "Bash(tail *)", "Bash(wc *)",
           "Bash(python3 quality/bin/*)"]   # cleat's own gates, which its instructions tell the agent to run
IGNORED = ["node_modules/", ".tsreport-*", "quality/.running/", "quality/.events.jsonl", ".cleat-gate-*.json"]


def clear_hooks(root):
    """The git hooks an install put in (husky, simple-git-hooks): a Python tree has none, and
    they would run the study's own commits through the project's tooling."""
    subprocess.run(["git", "-C", root, "config", "--unset", "core.hooksPath"], capture_output=True)
    hooks = os.path.join(root, ".git", "hooks")
    for name in os.listdir(hooks) if os.path.isdir(hooks) else []:
        if not name.endswith(".sample"):
            os.remove(os.path.join(hooks, name))


def prepare(clones, task, arm, root):
    draw.tree(os.path.join(clones, task["repo"]), task["parent"], root)
    subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)   # install hooks expect a repository
    code, said = tsdraw.install_recipe(root, task["recipe"])
    clear_hooks(root)
    with open(os.path.join(root, ".gitignore"), "a") as handle:
        handle.write("\n" + "\n".join(IGNORED) + "\n")
    run.git(root, "add", "-A")
    run.git(root, "commit", "-q", "-m", "base")
    info = dict(install=code)
    if arm.split("-")[0] != "plain":
        checkout = run.CHECKOUTS[arm.split("-")[0]]
        out = subprocess.run(["python3", os.path.join(checkout, "quality", "bin", "attach.py"), "--into", root],
                             capture_output=True, text=True, env=run.clean_env())
        info["attach"] = out.stdout[-3000:] + out.stderr[-1000:]
        run.git(root, "add", "-A")
        run.git(root, "commit", "-q", "-m", "attach cleat")
        gate = subprocess.run(["python3", "quality/bin/gate.py"], cwd=root, capture_output=True, text=True, env=run.clean_env())
        info["day_one_gate"] = gate.returncode
        info["day_one_tail"] = gate.stdout[-1500:]
        info["hooks"] = sorted(json.load(open(os.path.join(root, ".claude", "settings.json"))).get("hooks", {}))
    return info


def agent_env(task):
    env = run.clean_env()
    env.update(tsdraw.ENV_EXTRA)
    env["PATH"] = ":".join(tsdraw.recipe_path(task["recipe"]) + [env["PATH"]])
    return env


def agent_command(task, clones, work, root=None):
    return ["claude", "-p", PROMPT.format(hint=task.get("hint") or "the project's test runner", text=task["text"]),
            "--model", run.MODEL, "--setting-sources", "project",
            "--settings", run.sandbox(clones, work, root), "--strict-mcp-config", "--permission-mode", "dontAsk",
            "--allowedTools", ",".join(ALLOWED), "--disallowedTools", "WebFetch,WebSearch,Agent",
            "--max-budget-usd", run.BUDGET, "--output-format", "stream-json", "--verbose", "--no-session-persistence"]


def agent(task, root, out_dir, clones, work):
    with open(os.path.join(out_dir, "transcript.jsonl"), "w") as handle:
        try:
            p = subprocess.run(agent_command(task, clones, work, root), cwd=root, stdout=handle, stderr=subprocess.PIPE,
                               text=True, timeout=run.TIMEOUT, env=agent_env(task))
            return dict(exit=p.returncode, stderr=p.stderr[-2000:])
        except subprocess.TimeoutExpired:
            return dict(exit="timeout")


def drop_modules(root):
    for dirpath, dirs, _ in os.walk(root):
        if "node_modules" in dirs:
            shutil.rmtree(os.path.join(dirpath, "node_modules"), ignore_errors=True)
            dirs.remove("node_modules")
        dirs[:] = [d for d in dirs if d != ".git"]


def one(clones, work, task, arm):
    out_dir = os.path.join(work, "runs", task["id"], arm)
    if os.path.isfile(os.path.join(out_dir, "score.json")):
        return task["id"], arm, "cached"
    if run.PARKED.is_set():
        return task["id"], arm, "skipped: usage limit"
    root = os.path.join(out_dir, "repo")
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)
    info = prepare(clones, task, arm, root)
    info["agent"] = agent(task, root, out_dir, clones, work)
    if run.limited(out_dir, info["agent"]):
        run.PARKED.set()
        return task["id"], arm, "parked: usage limit, run again after it resets"
    info["score"] = tsscore.score_run(clones, task, arm, root, out_dir)
    with open(os.path.join(out_dir, "score.json"), "w") as handle:
        json.dump(info, handle, indent=1)
    drop_modules(root)
    return task["id"], arm, "resolved=%s" % info["score"].get("resolved")


def main(clones, work, jobs="4", repeats="1", *ids):
    clones, work = os.path.abspath(clones), os.path.abspath(work)
    with open(os.path.join(work, "tasks.json")) as handle:
        tasks = json.load(handle)["tasks"]
    if ids:
        tasks = [t for t in tasks if t["id"] in ids]
    runs = ["%s-%d" % (arm, r) for r in range(1, int(repeats) + 1) for arm in run.ARMS] if int(repeats) > 1 else run.ARMS
    pairs = [(t, arm) for t in tasks for arm in runs]
    with concurrent.futures.ThreadPoolExecutor(int(jobs)) as pool:
        for result in pool.map(lambda p: one(clones, work, *p), pairs):
            print(*result, flush=True)


def prepare_only(clones, work, task_id, arm):
    """The tree one run would start from, and the agent command it would run — for checking the
    preparation without starting an agent."""
    clones, work = os.path.abspath(clones), os.path.abspath(work)
    task = next(t for t in json.load(open(os.path.join(work, "tasks.json")))["tasks"] if t["id"] == task_id)
    root = os.path.join(work, "prepared", task_id, arm, "repo")
    shutil.rmtree(os.path.dirname(root), ignore_errors=True)
    os.makedirs(os.path.dirname(root))
    info = prepare(clones, task, arm, root)
    info["command"] = agent_command(task, clones, work)
    print(json.dumps({k: v for k, v in info.items() if k != "attach"}, indent=1)[:4000])
    return root, info


if __name__ == "__main__":
    if sys.argv[1:2] == ["--prepare-only"]:
        prepare_only(*sys.argv[2:6])
    else:
        main(*sys.argv[1:])

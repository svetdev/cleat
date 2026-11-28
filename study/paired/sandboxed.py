#!/usr/bin/env python3
"""sandboxed — can the agent itself run the arm's gates, in the sandbox its Bash runs in?

  sandboxed.py <clones dir> <work dir> <arm> [jobs] [task id …]

proof.py runs the gates as the Stop hook does, outside the sandbox. The agent runs them from
its own Bash, inside it, with no network; a suite that opens sockets can hang there, and an
agent whose gate never finishes cannot work under it. Per task proof.json calls proven, the
arm's tree is prepared and one `claude -p` run, with run.py's model, flags and sandbox, is
asked to run `python3 quality/bin/gate.py --changed` and repeat its last line. The task is
usable when that line says every gate passed within LIMIT seconds. A few cents a task.
Writes <work>/sandboxed.json: {task: {usable, seconds, said}}.
"""

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run   # noqa: E402

LIMIT = 240
ASK = ("Run exactly this one command, once, in the foreground with a timeout of 4 minutes, and change nothing: "
       "`python3 quality/bin/gate.py --changed`. Then reply with the last line it printed and nothing else. "
       "If it did not finish in time, reply with the two words: timed out")


def one(clones, work, task, arm):
    root = os.path.join(work, "sandboxed", task["id"], "repo")
    shutil.rmtree(os.path.dirname(root), ignore_errors=True)
    os.makedirs(os.path.join(work, "runs", task["id"], "probe"), exist_ok=True)
    os.makedirs(os.path.dirname(root))
    row = dict(usable=False, seconds=None, said="")
    try:
        run.prepare(clones, task, arm, root)
        start = time.time()
        cmd = ["claude", "-p", ASK, "--model", run.MODEL, "--setting-sources", "project", "--settings", run.sandbox(clones, work),
               "--strict-mcp-config", "--permission-mode", "dontAsk", "--allowedTools", ",".join(run.ALLOWED),
               "--disallowedTools", "WebFetch,WebSearch,Agent", "--max-budget-usd", "1", "--output-format", "json", "--no-session-persistence"]
        out = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=900, env=dict(run.clean_env(), CLEAT_HOOKS="off"))
        result = json.loads(out.stdout) if out.stdout.strip().startswith("{") else {}
        row.update(seconds=round(time.time() - start), said=str(result.get("result", out.stdout[-200:]))[:200], cost=result.get("total_cost_usd"))
        row["usable"] = "all passed" in row["said"] and row["seconds"] <= LIMIT
    except Exception as problem:   # a hung probe is the answer, not a crash
        row["said"] = repr(problem)[:200]
    shutil.rmtree(os.path.dirname(root), ignore_errors=True)
    return task["id"], row


def main(clones, work, arm, jobs="4", *ids):
    clones, work = os.path.abspath(clones), os.path.abspath(work)
    proven = json.load(open(os.path.join(work, "proof.json")))
    tasks = [t for t in json.load(open(os.path.join(work, "tasks.json")))["tasks"] if proven.get(t["id"], {}).get("proven") and (not ids or t["id"] in ids)]
    path = os.path.join(work, "sandboxed.json")
    out = json.load(open(path)) if os.path.isfile(path) else {}
    with concurrent.futures.ThreadPoolExecutor(int(jobs)) as pool:
        for name, row in pool.map(lambda t: one(clones, work, t, arm), [t for t in tasks if t["id"] not in out]):
            out[name] = row
            print("%-46s %s %ss  %s" % (name, "usable  " if row["usable"] else "UNUSABLE", row["seconds"], row["said"][:90]), flush=True)
            with open(path, "w") as handle:
                json.dump(out, handle, indent=1)
    print("%d of %d proven tasks usable in the agent's sandbox, $%.2f" % (sum(r["usable"] for r in out.values()), len(out), sum(r.get("cost") or 0 for r in out.values())))


if __name__ == "__main__":
    main(*sys.argv[1:])

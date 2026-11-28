#!/usr/bin/env python3
"""grow — build the app of spec.json one request at a time, and count what each request broke.

  grow.py --setup <work dir>                      install the seed's packages, once (the only step that uses the network)
  grow.py --prepare <work dir> <chain>            make the chain's tree and attach its arm; no agent
  grow.py <work dir> <upto> <chain> [chain …]     run each chain through feature <upto>, the chains side by side

A chain is `<arm>-<n>`: plain-1, cleat-2, tests-1. It starts from seed/ (a package.json,
a tsconfig.json and the installed packages; no source, no tests) and for feature N starts
a fresh `claude -p` session in the chain's tree with that feature's request and nothing
else: no conversation carries over. Claude Code keeps an auto-memory directory per tree,
which a session may write to and the next would read; `memory_files` in each score counts
what is there, and the driver neither clears nor fills it.
When the session ends, the hidden tests of features 1..N run against the tree (hidden.py)
and the tree is committed. A change that breaks an earlier feature is kept, not rolled
back: the next request is made of the tree as it is.

The arms:
  plain   no cleat.
  cleat   attach.py --into the seed, which on a project with no source attaches only the
          `commands` gate; `attach.py --add` once after feature 1, when there is code for
          complexity, duplication and escapes to read (REATTACH_AFTER).
  tests   cleat, with the test command replaced by one that runs the suite under coverage,
          fails when no test ran any source or a test fails, and then holds the change to
          80% changed-line coverage. The coverage check is inside the command, as in
          paired/run.py, because gate.py runs the `changed_coverage` gate only with
          --postflight and the Stop hook does not pass it.

Per step, in <work dir>/chains/<chain>/steps/NN/score.json: each feature's pass or fail,
`regressed` (passed at the step before, fails now), the session's cost and turns, the
lines and files of the codebase, the test files, and `opened`: the share of the files
that existed before the session which it read, edited or named in a command.

A step with a score.json is done and is skipped; a step without one starts again from
the last commit. An API error (529, a dropped connection) is retried, RETRIES times with
a growing wait, and then written to error.json, leaving the step to run again. Only a
message that names the plan's usage limit parks the run.

Same model, budget, flags and sandbox as paired/run.py, with one difference: the sandbox
lets Bash bind and reach localhost, because trying a server means starting it. Bash has
no other network. The agent's own PORT is set per chain so side-by-side chains do not
meet on 3000. STUDY_MODEL names another model; CLEAT_CHECKOUT another copy of cleat.
"""

import concurrent.futures
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "paired"))
import hidden   # noqa: E402
import run      # noqa: E402  the paired harness: its model, budget, time limit and environment

CLEAT = os.environ.get("CLEAT_CHECKOUT", REPO)
ARMS = ("plain", "cleat", "tests")
REATTACH_AFTER = 1
RETRIES = int(os.environ.get("STUDY_RETRIES", "4"))   # 1: an errored session is not tried again
PROMPT = """You are working on the project in this directory. Do what is asked below. There is no network and nothing can be installed: use what is already in node_modules. When it is done, stop.

--- request ---
{text}
"""
ALLOWED = ["Read", "Glob", "Grep", "Edit(./**)", "Write(./**)",
           "Bash(npm start *)", "Bash(npm test *)", "Bash(npm run *)", "Bash(node *)", "Bash(npx tsc *)",
           "Bash(curl *)", "Bash(sleep *)", "Bash(kill *)", "Bash(pkill *)", "Bash(lsof *)", "Bash(cd *)", "Bash(mkdir *)", "Bash(echo *)",
           "Bash(git status *)", "Bash(git diff *)", "Bash(git log *)", "Bash(git show *)",
           "Bash(ls *)", "Bash(head *)", "Bash(tail *)", "Bash(wc *)",
           "Bash(python3 quality/bin/*)"]   # cleat's own gates, which its instructions tell the agent to run
REPORT = "quality/.coverage/lcov.info"
SUITE = ("mkdir -p quality/.coverage; rm -f %s; node --test --experimental-test-coverage --test-reporter=lcov "
         "--test-reporter-destination=%s --test-reporter=spec --test-reporter-destination=stdout >quality/.suite.log 2>&1; "
         "code=$?; tail -20 quality/.suite.log; grep -q '^SF:' %s 2>/dev/null || { echo 'FAIL: the tests ran no source file, "
         "so coverage cannot be judged: this project needs tests that run its code'; exit 1; }; test $code -eq 0 || exit $code; "
         "python3 quality/bin/check-changed-coverage.py" % (REPORT, REPORT, REPORT))
NOT_THE_APP = ("quality/", ".claude/", "node_modules/")
NOT_THE_APP_FILES = {"CLAUDE.md", "quality.json", "package-lock.json", ".gitignore"}
CODE = (".ts", ".tsx", ".mts", ".js", ".mjs", ".cjs")
TEST_NAME = re.compile(r"(\.|/)(test|spec)\.[a-z]+$|(^|/)(tests?|__tests__)/")
LIMIT = re.compile(r"usage limit|hit your limit|limit reached", re.I)
PARKED = threading.Event()


class Parked(Exception):
    """The plan's usage limit: nothing more can run until it resets."""


def features():
    with open(os.path.join(HERE, "spec.json")) as handle:
        return json.load(handle)["features"]


def sh(root, *command, check=True):
    return subprocess.run(command, cwd=root, capture_output=True, text=True, env=run.clean_env(), check=check)


# --- the tree

def setup(work):
    seed = os.path.join(work, "seed")
    os.makedirs(seed, exist_ok=True)
    for name in ("package.json", "tsconfig.json", "package-lock.json"):
        shutil.copyfile(os.path.join(HERE, "seed", name), os.path.join(seed, name))
    done = subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=seed, env=run.clean_env())
    return done.returncode


def new_tree(work, root):
    """The seed as a git repository: what `npm init` and an install would leave, and no code."""
    seed = os.path.join(work, "seed")
    if not os.path.isdir(os.path.join(seed, "node_modules")):
        sys.exit("no packages in %s: run grow.py --setup %s first" % (seed, work))
    os.makedirs(root)
    for name in ("package.json", "tsconfig.json", "package-lock.json"):
        shutil.copyfile(os.path.join(seed, name), os.path.join(root, name))
    shutil.copyfile(os.path.join(HERE, "seed", "gitignore"), os.path.join(root, ".gitignore"))
    shutil.copytree(os.path.join(seed, "node_modules"), os.path.join(root, "node_modules"), symlinks=True)
    run.git(root, "init", "-q")
    commit(root, "seed")


def commit(root, message):
    run.git(root, "add", "-A")
    run.git(root, "commit", "-q", "--allow-empty", "-m", message)


def add_tests(root):
    """The tests arm's policy: the suite under coverage as the test command, and changed-line coverage."""
    path = os.path.join(root, "quality.json")
    with open(path) as handle:
        config = json.load(handle)
    config["commands"] = [c for c in config.get("commands", []) if c.get("name") != "tests"] + [{"name": "tests", "run": SUITE, "needs": ["node"]}]
    config["changed_coverage"] = {"report": REPORT, "minimum": 0.8, "min_lines": run.COVERAGE_MIN_LINES}
    with open(path, "w") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    with open(os.path.join(root, ".gitignore"), "a+") as handle:
        handle.seek(0)
        if "quality/.coverage/" not in handle.read():
            handle.write("\nquality/.coverage/\nquality/.suite.log\n")


def attach(root, arm, *flags):
    """attach.py on the tree, then the arm's own additions; what attach said, and the gates' verdict today."""
    if arm == "plain":
        return {}
    out = sh(root, "python3", os.path.join(CLEAT, "quality", "bin", "attach.py"), "--into", root, *flags, check=False)
    if arm == "tests":
        add_tests(root)
    commit(root, "attach cleat" + (" " + " ".join(flags) if flags else ""))
    gate = sh(root, "python3", "quality/bin/gate.py", check=False)
    with open(os.path.join(root, "quality.json")) as handle:
        gates = sorted(k for k in json.load(handle) if k != "project")
    return dict(said=out.stdout[-3000:] + out.stderr[-1000:], gates=gates, gate_exit=gate.returncode, gate_tail=gate.stdout[-800:])


def prepare(work, chain):
    arm = chain.split("-")[0]
    if arm not in ARMS:
        sys.exit("no arm %r: a chain is one of %s, a dash and a number" % (arm, ", ".join(ARMS)))
    home = os.path.join(work, "chains", chain)
    root = os.path.join(home, "repo")
    info_path = os.path.join(home, "chain.json")
    if os.path.isfile(info_path):
        with open(info_path) as handle:
            return root, json.load(handle)
    if os.path.isdir(home):
        sys.exit("%s exists without a chain.json: look at it, and remove it by hand to start the chain again" % home)
    new_tree(work, root)
    info = dict(chain=chain, arm=arm, model=run.MODEL, cleat=None if arm == "plain" else CLEAT,
                port=4100 + zlib.crc32(chain.encode()) % 800, attach=attach(root, arm))
    with open(info_path, "w") as handle:
        json.dump(info, handle, indent=1)
    return root, info


# --- the agent

def hidden_paths(work):
    """What no agent may read: this directory (the requests to come, the hidden tests, the reference),
    the proof's trees, and every chain's steps and the other chains' trees."""
    chains = os.path.join(work, "chains")
    return [HERE, os.path.join(work, "proof"), os.path.join(work, "proof.json")] + [os.path.join(chains, name) for name in sorted(os.listdir(chains))]


def sandbox(work, home):
    # As paired/run.py: Bash in the OS sandbox with the allowlist still asked, the hidden paths denied to
    # Bash and to the file tools. The chain's own tree is the one path under chains/ left readable.
    own = os.path.join(home, "repo")
    paths = [p for p in hidden_paths(work) if os.path.abspath(p) != os.path.abspath(home)] + [os.path.join(home, "steps"), os.path.join(home, "chain.json")]
    deny = ["%s(/%s%s)" % (tool, p, "/**" if os.path.isdir(p) else "") for p in paths for tool in ("Read", "Grep", "Glob", "Edit", "Write")]
    assert not any(os.path.abspath(own).startswith(os.path.abspath(p) + os.sep) for p in paths)
    return json.dumps({"permissions": {"deny": deny},
                       "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": False, "failIfUnavailable": True,
                                   "filesystem": {"denyRead": paths}, "network": {"allowLocalBinding": True}}})


def agent(work, home, info, text, out_dir):
    root = os.path.join(home, "repo")
    command = ["claude", "-p", PROMPT.format(text=text), "--model", info["model"], "--setting-sources", "project",
               "--settings", sandbox(work, home), "--strict-mcp-config", "--permission-mode", "dontAsk",
               "--allowedTools", ",".join(ALLOWED), "--disallowedTools", "WebFetch,WebSearch,Agent",
               "--max-budget-usd", run.BUDGET, "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    env = dict(run.clean_env(), PORT=str(info["port"]))
    with open(os.path.join(out_dir, "transcript.jsonl"), "w") as handle:
        process = subprocess.Popen(command, cwd=root, stdout=handle, stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
        try:
            _, stderr = process.communicate(timeout=run.TIMEOUT)
            ended = dict(exit=process.returncode, stderr=stderr[-2000:])
        except subprocess.TimeoutExpired:
            ended = dict(exit="timeout", stderr="")
        finally:
            sweep(process.pid, info["port"])
            process.wait()
    return ended


def sweep(group, port):
    """End whatever the session left running: its process group, and anything still on the chain's port."""
    try:
        os.killpg(group, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    left = subprocess.run(["lsof", "-ti", "tcp:%d" % port], capture_output=True, text=True).stdout.split()
    for pid in left:
        try:
            os.kill(int(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def result_of(out_dir):
    """The transcript's last result event, and every tool call in it."""
    last, calls = None, []
    with open(os.path.join(out_dir, "transcript.jsonl"), errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "result":
                last = event
            content = (event.get("message") or {}).get("content") if event.get("type") == "assistant" else None
            for item in content if isinstance(content, list) else []:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    calls.append((item.get("name"), item.get("input") or {}))
    return last, calls


def how_ended(out_dir, ended):
    """done: the session ran to an end of its own (a finished turn, the budget, the turn or time limit) and
    its tree is an outcome. limit: the plan's usage limit. error: anything else, which is the API or the
    harness and not the agent, and is tried again."""
    last, _ = result_of(out_dir)
    if ended["exit"] == "timeout":
        return "done"
    if last and (not last.get("is_error") or str(last.get("subtype", "")).startswith("error_max")):
        return "done"
    return "limit" if LIMIT.search(json.dumps(last or {}) + ended.get("stderr", "")) else "error"


def session(work, home, info, text, out_dir):
    """One session that ended of its own, after as many tries as that took: {exit, attempts, errors}."""
    root, errors = os.path.join(home, "repo"), []
    for attempt in range(1, RETRIES + 1):
        ended = agent(work, home, info, text, out_dir)
        kind = how_ended(out_dir, ended)
        if kind == "done":
            return dict(ended, attempts=attempt, errors=errors)
        last, _ = result_of(out_dir)
        errors.append(dict(kind=kind, exit=ended["exit"], said=(json.dumps(last)[-600:] if last else "no result event") + " | " + ended["stderr"][-400:]))
        os.replace(os.path.join(out_dir, "transcript.jsonl"), os.path.join(out_dir, "failed-%d.jsonl" % attempt))
        restore(root)
        if kind == "limit":
            PARKED.set()
            raise Parked(errors[-1]["said"])
        if attempt < RETRIES:
            time.sleep(60 * 2 ** (attempt - 1))
    with open(os.path.join(out_dir, "error.json"), "w") as handle:
        json.dump(errors, handle, indent=1)
    raise RuntimeError("the session did not run in %d tries: %s" % (RETRIES, errors[-1]["said"][:300]))


def restore(root):
    run.git(root, "reset", "-q", "--hard", "HEAD")
    run.git(root, "clean", "-q", "-fd")


# --- what a step measures

def app_files(root):
    """The project's own files: tracked or new, not ignored, not cleat's and not the packages."""
    names = sh(root, "git", "ls-files", "-co", "--exclude-standard").stdout.split("\n")
    return sorted(n for n in filter(None, names) if not n.startswith(NOT_THE_APP) and n not in NOT_THE_APP_FILES)


def lines(root, names):
    total = 0
    for name in names:
        with open(os.path.join(root, name), errors="replace") as handle:
            total += sum(1 for _ in handle)
    return total


def opened(root, before, calls):
    """Of the files there before the session, the ones it read, edited or named in a command."""
    touched, said = set(), ""
    for name, given in calls:
        path = given.get("file_path")
        if name in ("Read", "Edit", "Write", "MultiEdit") and path:
            touched.add(os.path.realpath(os.path.join(root, path)))
        if name == "Bash":
            said += " " + str(given.get("command", ""))
    seen = [f for f in before if os.path.realpath(os.path.join(root, f)) in touched or f in said]
    return dict(before=len(before), opened=len(seen), share=round(len(seen) / len(before), 3) if before else None)


def measure(root, out_dir, before):
    last, calls = result_of(out_dir)
    now = app_files(root)
    code = [f for f in now if f.endswith(CODE)]
    tests = [f for f in code if TEST_NAME.search(f)]
    stat = sh(root, "git", "diff", "--numstat", "HEAD").stdout.split("\n")
    changed = [line.split("\t") for line in stat if line.strip()]
    return dict(cost=(last or {}).get("total_cost_usd"), turns=(last or {}).get("num_turns"), seconds=round(((last or {}).get("duration_ms") or 0) / 1000),
                ended=(last or {}).get("subtype"), tool_calls=len(calls), files=len(now), code_files=len(code), code_lines=lines(root, code),
                test_files=len(tests), test_lines=lines(root, tests), new_files=len(set(now) - set(before)),
                changed_tracked=len(changed), opened=opened(root, before, calls))


def memory_files(root):
    """Files in the auto-memory directory Claude Code keeps for this tree: what a session left for the next one."""
    where = os.path.join(os.path.expanduser("~/.claude/projects"), re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(root)), "memory")
    return len(os.listdir(where)) if os.path.isdir(where) else 0


def gate_now(root, arm):
    if arm == "plain":
        return None
    gate = sh(root, "python3", "quality/bin/gate.py", check=False)
    failing = re.findall(r"^\s*FAIL\s+(\S+)", gate.stdout, re.M)
    return dict(exit=gate.returncode, failing=failing)


def previous(home, number):
    path = os.path.join(home, "steps", "%02d" % (number - 1), "score.json")
    if not os.path.isfile(path):
        return {}
    with open(path) as handle:
        return json.load(handle)["features"]


def step(work, home, info, feature):
    number, root = feature["id"], os.path.join(home, "repo")
    out_dir = os.path.join(home, "steps", "%02d" % number)
    if os.path.isfile(os.path.join(out_dir, "score.json")):
        return "cached"
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)
    restore(root)
    before = app_files(root)
    ended = session(work, home, info, feature["request"], out_dir)
    score = dict(chain=info["chain"], arm=info["arm"], feature=number, name=feature["name"], agent=ended, **measure(root, out_dir, before))
    score["gate"] = gate_now(root, info["arm"])
    score["memory_files"] = memory_files(root)
    results = hidden.run(root, number)
    score["features"] = {str(n): dict(ok=r["ok"], said=r["said"][:500]) for n, r in results.items()}
    was = previous(home, number)
    score["own"] = results[number]["ok"]
    score["passing"] = sum(r["ok"] for r in results.values())
    score["regressed"] = [n for n, r in results.items() if n < number and not r["ok"] and was.get(str(n), {}).get("ok")]
    score["mended"] = [n for n, r in results.items() if n < number and r["ok"] and was.get(str(n), {}).get("ok") is False]
    commit(root, "feature %02d: %s" % (number, feature["name"]))
    if number == REATTACH_AFTER:
        score["reattach"] = attach(root, info["arm"], "--add")
    with open(os.path.join(out_dir, "score.json"), "w") as handle:
        json.dump(score, handle, indent=1)
    return "own=%s passing=%d/%d regressed=%s $%.2f" % (score["own"], score["passing"], number, score["regressed"], score["cost"] or 0)


def chain(work, name, upto):
    root, info = prepare(work, name)
    home = os.path.dirname(root)
    for feature in features()[:upto]:
        if PARKED.is_set():
            return name, "parked: usage limit, run again after it resets"
        try:
            said = step(work, home, info, feature)
        except Parked:
            return name, "parked at feature %d: usage limit, run again after it resets" % feature["id"]
        except RuntimeError as error:
            return name, "stopped at feature %d: %s" % (feature["id"], error)
        print("%s %02d %s" % (name, feature["id"], said), flush=True)
    return name, "through feature %d" % upto


def main(work, upto, *chains):
    work = os.path.abspath(work)
    os.makedirs(os.path.join(work, "chains"), exist_ok=True)
    for name in chains:   # one at a time: each must see the others' directories to deny them
        prepare(work, name)
    with concurrent.futures.ThreadPoolExecutor(len(chains)) as pool:
        for name, said in pool.map(lambda c: chain(work, c, int(upto)), chains):
            print("%s: %s" % (name, said), flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "--setup":
        sys.exit(setup(os.path.abspath(sys.argv[2])))
    if sys.argv[1] == "--prepare":
        os.makedirs(os.path.join(os.path.abspath(sys.argv[2]), "chains"), exist_ok=True)
        print(json.dumps(prepare(os.path.abspath(sys.argv[2]), sys.argv[3])[1], indent=1))
        sys.exit(0)
    main(*sys.argv[1:])

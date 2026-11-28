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
import signal
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
# One suite run under coverage, its report and pytest's exit code. pytest exits 0 or 1 when the tests
# ran; anything else (no tests, a broken plugin) leaves a report of import-time lines only, on which
# every function body reads as not run. A file that cannot be collected stops a plain run the same
# way, so the run goes on past it, and a gate that reads the report first checks the suite ran.
SUITE_REPORT = ("rm -f .coverage quality/.coverage.xml; "
                ".venv/bin/python -m coverage run --omit='%s' -m pytest -q -p no:cacheprovider --continue-on-collection-errors >quality/.suite.log 2>&1; "
                "echo $? >quality/.suite-exit; "
                ".venv/bin/python -m coverage xml -q -o quality/.coverage.xml >/dev/null 2>&1; " % COVERAGE_OMIT)
SUITE_RAN = ('test "$(cat quality/.suite-exit)" -le 1 || { echo "FAIL: the test suite did not run (pytest exit '
             '$(cat quality/.suite-exit)), so coverage cannot be judged:"; tail -5 quality/.suite.log; exit 1; }; ')
SUITE_SECONDS = int(os.environ.get("STUDY_SUITE_SECONDS", "600"))   # a suite slower than this under coverage cannot be a gate at every stop
SUITE_IGNORED = "\n.coverage\n.coverage.*\nquality/.coverage.xml\nquality/.suite.log\nquality/.suite-exit\n"
COVERAGE_MIN_LINES = 5   # most of these tasks change fewer executable lines than the check's default of 20


def install_coverage(root):
    venv_python = os.path.join(root, ".venv", "bin", "python")
    subprocess.run([venv_python, "-m", "pip", "install", "-q", "--disable-pip-version-check", "coverage"], check=True, capture_output=True)


# What a language's suite needs to be a coverage gate: the command that runs it and writes the
# report, the check that it ran, where the report is, which of CRAP's readers reads it, what
# git must ignore, and how the tool gets into the tree. tsrun.py builds one per task.
PYTHON = dict(suite=SUITE_REPORT, ran=SUITE_RAN, report="quality/.coverage.xml", reader="cobertura", ignored=SUITE_IGNORED, install=install_coverage)

# Arms with the project's own lint and format as a gate at every stop: STUDY_RULES="rules". After
# attach, ruff goes into .venv, project_rules.py is copied in as quality/project-rules.py and one
# `commands` entry runs it. It judges what gates/rules.py counts afterwards, by the same rule.
RULES_ARMS = set(filter(None, os.environ.get("STUDY_RULES", "").split(",")))
RUFF = os.environ.get("STUDY_RUFF_VERSION", "0.17.6")

# Arms with CRAP as a gate at every stop: STUDY_CRAP="crap". After attach, coverage goes into .venv,
# quality.json gets a `crap` section (threshold 8, lizard over the complexity gate's sources, a
# Cobertura report), the suite runs once under coverage and the functions over the gate that day
# go into the baseline. One `commands` entry then runs the suite under coverage and the gate itself
# on the changed files: gate.py's hook pass runs CRAP only as an estimate that never fails.
# STUDY_COVERAGE and STUDY_CRAP may name the same arm; it then carries both gates.
# An arm may name its own threshold: STUDY_CRAP="crap8=8,crap30=30".
STRIP_TESTS = bool(os.environ.get("STUDY_STRIP_TESTS"))   # hand the agent a tree without the tests of the code it is to change
CRAP_THRESHOLDS = dict((x.partition("=")[0], float(x.partition("=")[2] or os.environ.get("STUDY_CRAP_THRESHOLD", "8")))
                       for x in filter(None, os.environ.get("STUDY_CRAP", "").split(",")))   # 8 is cleat's; the metric's authors used 30
CRAP_ARMS = set(CRAP_THRESHOLDS)

# Arms with the public API as a gate at every stop: STUDY_API="api". After attach, quality.json gets
# a `public_api` section over the project's packages (src/, or the top-level directories that are
# packages), tests left out, and the surface as it stands is the baseline. A recorded signature
# that is gone, renamed or changed is refused; one added is a note.
API_ARMS = set(filter(None, os.environ.get("STUDY_API", "").split(",")))
NOT_SHIPPED = {"tests", "test", "testing", "t", "docs", "doc", "examples", "benchmarks", "scripts", "quality"}

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


def suite_once(root, stack=PYTHON):
    """The suite under coverage once, as the gate's command runs it: {exit, last line}, or {timeout}."""
    suite = subprocess.Popen(stack["suite"], shell=True, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             env=clean_env(), start_new_session=True)
    try:
        suite.wait(timeout=SUITE_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(suite.pid, signal.SIGKILL)   # the shell's children too: a killed shell leaves pytest running
        return dict(timeout=SUITE_SECONDS)
    read = lambda name: open(os.path.join(root, "quality", name), errors="replace").read().strip() if os.path.isfile(os.path.join(root, "quality", name)) else ""   # noqa: E731
    return dict(exit=int(read(".suite-exit") or 9), said=(read(".suite.log").splitlines() or [""])[-1][-200:])


def add_coverage(root, stack=PYTHON):
    stack["install"](root)
    path = os.path.join(root, "quality.json")
    with open(path) as handle:
        config = json.load(handle)
    config["changed_coverage"] = {"report": stack["report"], "minimum": 0.8, "min_lines": COVERAGE_MIN_LINES}
    config.setdefault("commands", []).append({"name": "changed-coverage", "run": stack["suite"] + stack["ran"] + "python3 quality/bin/check-changed-coverage.py"})
    with open(path, "w") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    with open(os.path.join(root, ".gitignore"), "a") as handle:
        handle.write(stack["ignored"])
    return dict(config["changed_coverage"], suite=suite_once(root, stack))


def add_rules(root):
    venv_python = os.path.join(root, ".venv", "bin", "python")
    subprocess.run([venv_python, "-m", "pip", "install", "-q", "--disable-pip-version-check", "ruff==" + RUFF], check=True, capture_output=True)
    shutil.copyfile(os.path.join(HERE, "project_rules.py"), os.path.join(root, "quality", "project-rules.py"))
    path = os.path.join(root, "quality.json")
    with open(path) as handle:
        config = json.load(handle)
    entry = {"name": "project-rules", "run": ".venv/bin/python quality/project-rules.py"}
    config.setdefault("commands", []).append(entry)
    with open(path, "w") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    return entry


def add_crap(root, threshold=8, stack=PYTHON):
    stack["install"](root)
    path = os.path.join(root, "quality.json")
    with open(path) as handle:
        config = json.load(handle)
    shape = config["complexity"]
    config["crap"] = {"threshold": threshold, "baseline": "quality/crap-baseline.json",
                      "complexity": {"tool": "lizard", "sources": shape["sources"], "languages": shape["languages"], "exclude": shape.get("exclude", [])},
                      stack["reader"]: {"sources": ".", "exports": stack["report"]}}
    config.setdefault("commands", []).append({"name": "crap", "run": stack["suite"] + stack["ran"] + "python3 quality/bin/check-crap.py --changed"})
    with open(path, "w") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    with open(os.path.join(root, ".gitignore"), "a") as handle:
        handle.write(stack["ignored"])
    suite = suite_once(root, stack)
    if "timeout" in suite:
        return dict(baseline="suite did not finish under coverage in %ds" % SUITE_SECONDS, suite=suite)
    out = subprocess.run(["python3", "quality/bin/check-crap.py", "--write-baseline"], cwd=root, capture_output=True, text=True, env=clean_env())
    return dict(baseline=out.returncode, said=(out.stdout + out.stderr)[-300:], suite=suite)


def packages(root):
    """Where the code a project ships lives: src/, or its top-level package directories."""
    if os.path.isdir(os.path.join(root, "src")):
        return ["src"]
    return sorted(d for d in os.listdir(root) if d not in NOT_SHIPPED and os.path.isfile(os.path.join(root, d, "__init__.py")))


def add_api(root):
    path = os.path.join(root, "quality.json")
    with open(path) as handle:
        config = json.load(handle)
    config["public_api"] = {"name": "api", "language": "python", "roots": packages(root), "skip_dirs": sorted(NOT_SHIPPED),
                            "baseline": "quality/api-baseline.json"}
    with open(path, "w") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    out = subprocess.run(["python3", "quality/bin/check-public-api.py", "--write-baseline"], cwd=root, capture_output=True, text=True, env=clean_env())
    return dict(config["public_api"], baseline=out.returncode, said=(out.stdout + out.stderr)[-300:])


def strip_tests(task, root):
    """With STUDY_STRIP_TESTS, the test files the commit touches are gone from the tree the agent
    gets, so the code it is to change stands untested, as it would in a project that tests little.
    Scoring lays the commit's own test files back. Returns the files removed."""
    gone = [t for t in task["tests"] if STRIP_TESTS and os.path.isfile(os.path.join(root, t))]
    for name in gone:
        os.remove(os.path.join(root, name))
    return gone


def prepare(clones, task, arm, root):
    draw.tree(os.path.join(clones, task["repo"]), task["parent"], root)
    stripped = strip_tests(task, root)
    with open(os.path.join(root, ".gitignore"), "a") as handle:
        handle.write("\n.venv/\n")
    draw.install_recipe(root, task["recipe"])
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    info = {"stripped": stripped} if stripped else {}
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
        added = (("coverage", COVERAGE_ARMS, add_coverage), ("rules", RULES_ARMS, add_rules),
                 ("crap", CRAP_ARMS, lambda tree: add_crap(tree, CRAP_THRESHOLDS[arm.split("-")[0]])), ("api", API_ARMS, add_api))
        for key, arms, add in added:
            if arm.split("-")[0] in arms:
                info[key] = add(root)
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


def keep_work(root, out_dir):
    """The files the agent changed, copied to <run>/agent before scoring lays the hidden tests over some of them."""
    names = subprocess.run(["git", "-C", root, "diff", "HEAD", "--name-only"], capture_output=True, text=True).stdout.split("\n")
    names += subprocess.run(["git", "-C", root, "ls-files", "--others", "--exclude-standard"], capture_output=True, text=True).stdout.split("\n")
    for name in filter(None, names):
        source, target = os.path.join(root, name), os.path.join(out_dir, "agent", name)
        if os.path.isfile(source):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(source, target)


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
    keep_work(root, out_dir)
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

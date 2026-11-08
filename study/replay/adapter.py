"""adapter — a language for the replay from a replay.json that whoever runs it fills in.

  {
    "name": "python",
    "languages": ["python"],                       cleat's names; production files by their suffixes
    "install": "uv sync --frozen",                 run in the tree's root
    "test": "uv run pytest -q {files} --junitxml={out}",
    "check": "uv run mypy src",                    optional: a compile or type-check
    "manifests": ["pyproject.toml", "uv.lock"],    a change to one means a reinstall
    "agent_commands": ["uv run pytest *"],         what the agent may run, as Bash(...) patterns
    "production": ["src/**"], "tests": ["tests/**"],   optional globs, over cleat's detection
    "hint": "uv run pytest <test file>",           optional: how the agent is told to run tests
    "env": {}, "tool_dirs": [], "build_dirs": [], "ignored": [], "keep_own": [], "public": ""
  }

The test command runs the files it is given (`{files}`; without it, every test) and writes JUnit
XML to `{out}`, which nearly every runner can. A test's id is its file, class and name; a test
that was skipped did not run. The check's errors are the output lines that name an error,
without their line and column, so a line moved above one is not a new error.
"""

import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "quality", "bin"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "paired"))
import summary                    # noqa: E402  cleat's own test-file rule
from extractors import languages  # noqa: E402
import draw                       # noqa: E402
import run                        # noqa: E402

REQUIRED = ("name", "install", "test", "manifests", "agent_commands")
TIMEOUT = 1800
BUILT = {"node_modules", "target", "dist", "build", ".venv", "venv", "__pycache__", "vendor", "quality"}
POSITION = re.compile(r"(:\d+(:\d+)?\b|\(\d+,\d+\))")
ERROR = re.compile(r"\berror\b", re.I)
PROMPT = """You are working in a {name} repository. Implement the change described below.

The project's dependencies are installed; run the tests with `{{hint}}`. Do not use the network. When the change is done, stop.

--- task ---
{{text}}
"""
BASE_TOOLS = ["Read", "Glob", "Grep", "Edit(./**)", "Write(./**)", "Bash(cd *)",
              "Bash(git status *)", "Bash(git diff *)", "Bash(git log *)", "Bash(git show *)",
              "Bash(ls *)", "Bash(head *)", "Bash(tail *)", "Bash(wc *)",
              "Bash(python3 quality/bin/*)"]   # cleat's own gates, which its instructions tell the agent to run
OWN = ["quality/.running/", "quality/.events.jsonl", ".cleat-gate-*.json", ".replay-junit-*.xml"]


def read(path):
    """The adapter's settings, or a SystemExit naming what is missing."""
    try:
        with open(path) as handle:
            spec = json.load(handle)
    except (OSError, ValueError) as err:
        raise SystemExit("adapter %s: %s" % (path, err))
    missing = [k for k in REQUIRED if not spec.get(k)]
    if missing:
        raise SystemExit("adapter %s: fill in %s" % (path, ", ".join(missing)))
    if "{out}" not in spec["test"]:
        raise SystemExit("adapter %s: the test command must write JUnit XML to {out}" % path)
    if not spec.get("production") and not spec.get("languages"):
        raise SystemExit("adapter %s: name its languages (cleat's names) or give production globs" % path)
    return spec


def glob_match(path, globs):
    return any(fnmatch.fnmatch(path, g) for g in globs)


def rules(spec):
    """(production, is_test) for a repository-relative path."""
    suffixes = languages.suffixes(spec.get("languages") or [])
    test_globs, prod_globs = spec.get("tests") or [], spec.get("production") or []

    def is_test(path):
        if test_globs:
            return glob_match(path, test_globs)
        return path.endswith(suffixes) and summary._is_test(path) and not summary._aside(path)

    def production(path):
        if is_test(path) or BUILT.intersection(path.split("/")[:-1]):
            return False
        return glob_match(path, prod_globs) if prod_globs else path.endswith(suffixes) and not summary._is_test(path)

    return production, is_test


def module_of(path):
    return os.path.splitext(path)[0].replace("/", ".").replace("\\", ".")


def owner(case, files):
    """The test file a JUnit testcase came from: its file attribute, or the file whose module
    path its class name starts with (pytest's default report carries no file)."""
    if case.get("file"):
        return case.get("file")
    name = case.get("classname") or ""
    hits = [f for f in files if name == module_of(f) or name.startswith(module_of(f) + ".") or module_of(f).endswith("." + name)]
    return max(hits, key=len) if hits else name


def parse_junit(text, files):
    """{test id: passed} from a JUnit XML report; skipped tests did not run and are left out."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}
    out = {}
    for case in root.iter("testcase"):
        if case.find("skipped") is not None:
            continue
        failed = case.find("failure") is not None or case.find("error") is not None
        test_id = "%s::%s::%s" % (owner(case, files), case.get("classname") or "", case.get("name") or "")
        out[test_id] = out.get(test_id, True) and not failed
    return out


def check_errors(output):
    return sorted({POSITION.sub("", line).strip() for line in output.split("\n") if ERROR.search(line)})


def shell(cmd, root, env=None, timeout=TIMEOUT):
    """(exit code or "timeout", the output's last 4,000 characters)."""
    try:
        p = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, errors="replace",
                           timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return "timeout", ""
    return p.returncode, (p.stdout + p.stderr)[-4000:]


def load(path):
    """The language class the replay's scripts expect, built from the adapter at `path`."""
    path = os.path.abspath(path)
    spec = read(path)
    prod_rule, test_rule = rules(spec)
    env_extra = {k: str(v) for k, v in (spec.get("env") or {}).items()}

    def environment():
        env = run.clean_env()
        env.update(env_extra)
        return env

    class Adapter:
        name = path   # what plan.json stores, so every script after the plan loads the same adapter
        label = spec["name"]
        public = re.compile(spec["public"], re.M) if spec.get("public") else re.compile(r"$^")
        manifests = tuple(spec["manifests"])
        prompt = PROMPT.format(name=spec["name"])
        allowed = BASE_TOOLS + ["Bash(%s)" % c for c in spec["agent_commands"]]
        ignored = list(spec.get("ignored") or []) + OWN
        tools = tuple(os.path.expanduser(d) for d in spec.get("tool_dirs") or [])
        keep_own = tuple(spec.get("keep_own") or [])
        runner_config = re.compile(r"$^")
        runs = threading.local()   # chains run as threads: each keeps its own runner output

        production = staticmethod(prod_rule)
        is_test = staticmethod(test_rule)

        @staticmethod
        def install(root, recipe=None, update=False):
            code, said = shell(spec["install"], root, environment())
            Adapter.runs.last = ["$ %s\nexit %s\n%s" % (spec["install"], code, said)]
            return (dict(cmd=spec["install"]), "ok") if code == 0 else (None, "install failed: " + said[-300:])

        @staticmethod
        def run_tests(root, change, label, timeout=None):
            """{test id: passed}; with `timeout`, None when the run took longer (mutate.py: killed)."""
            files = list(change["tests"])
            out = os.path.join(root, ".replay-junit-%s.xml" % label)
            cmd = spec["test"].replace("{files}", " ".join(map(shlex.quote, files))).replace("{out}", shlex.quote(out))
            code, said = shell(cmd, root, environment(), timeout or TIMEOUT)
            Adapter.runs.last = ["$ %s\nexit %s\n%s" % (cmd, code, said)]
            if timeout and code == "timeout":
                return None
            try:
                with open(out, errors="replace") as handle:
                    text = handle.read()
                os.remove(out)
            except OSError:
                return {}
            return parse_junit(text, files)

        @staticmethod
        def output():
            return getattr(Adapter.runs, "last", None) or []

        @staticmethod
        def suite(root):
            """Every test file the tree holds, and the check where there is one; `silent` are the
            test files that reported no test."""
            listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root,
                                    capture_output=True, text=True).stdout.split("\n")
            files = sorted({p for p in listed if p and test_rule(p) and os.path.isfile(os.path.join(root, p))})
            tests = Adapter.run_tests(root, dict(tests=files), "suite")
            said = Adapter.output()
            errors = check_errors(shell(spec["check"], root, environment())[1]) if spec.get("check") else []
            Adapter.runs.last = said
            silent = [f for f in files if not any(t.startswith(f + "::") for t in tests)]
            return dict(tests=tests, errors=errors, silent=silent)

        @staticmethod
        def validate(clones, work, cand):
            return validate(Adapter, clones, work, cand)

        @staticmethod
        def hint(root, files):
            return spec.get("hint") or spec["test"].replace("{files}", "<test file>").replace("{out}", "report.xml")

        @staticmethod
        def env(recipe):
            return environment()

        @staticmethod
        def drop_build(root):
            for d in spec.get("build_dirs") or []:
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)

        @staticmethod
        def before_check(root):
            pass

        @staticmethod
        def sandbox_extra(settings):
            writable = [os.path.expanduser(d) for d in spec.get("writable") or []]
            if writable:
                settings["sandbox"]["filesystem"].setdefault("allowWrite", []).extend(writable)

    return Adapter


def split_files(lang, cand):
    """(production files, other files, the test files among them that still exist)."""
    prods = [p for _, _, p in cand["files"] if lang.production(p)]
    others = [p for _, _, p in cand["files"] if not lang.production(p)]
    deleted = {q for s, _, q in cand["files"] if s == "D"}
    return prods, others, [p for p in others if lang.is_test(p) and p not in deleted]


def before_after(lang, clone, cand, root):
    """(task fields, why) on a tree at the change's parent with its dependencies installed."""
    prods, others, tests = split_files(lang, cand)
    if not tests:
        return None, "no runnable test file"
    draw.overlay(clone, cand["sha"], root, others)
    if any(os.path.basename(p) in lang.manifests for p in others) and not lang.install(root)[0]:
        return None, "reinstall failed"
    before = lang.run_tests(root, dict(tests=tests), "before")
    draw.overlay(clone, cand["sha"], root, prods)
    after = lang.run_tests(root, dict(tests=tests), "after")
    if not after:
        return None, "tests did not run at the commit"
    if not all(after.values()):
        return None, "%d test(s) fail at the commit" % sum(1 for v in after.values() if not v)
    f2p = sorted(t for t, ok in after.items() if ok and not before.get(t, False))
    if not f2p:
        return None, "no fail-to-pass test"
    p2p = sorted(t for t, ok in after.items() if ok and before.get(t, False))
    return dict(tests=tests, f2p=f2p, p2p=p2p, overlay=others, hint=lang.hint(root, tests)), "ok"


def validate(lang, clones, work, cand):
    """(task fields, why): the change's tests fail before it and pass after it on upstream's tree."""
    clone = os.path.join(clones, cand["repo"])
    root = os.path.join(work, "validate", cand["repo"] + "-" + cand["sha"][:8])
    try:
        draw.tree(clone, cand["parent"], root)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        recipe, why = lang.install(root)
        if recipe is None:
            return None, why
        task, why = before_after(lang, clone, cand, root)
        return (dict(task, recipe=recipe) if task else None), why
    finally:
        shutil.rmtree(root, ignore_errors=True)

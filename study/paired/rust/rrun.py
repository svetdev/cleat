#!/usr/bin/env python3
"""rrun — the paired runs on Rust tasks: run.py's arms, agent and scoring, with the Rust
tree, toolchain and tests swapped in.

  rrun.py <clones dir> <work dir> [jobs] [repeats] [task id …]

STUDY_ARMS and CLEAT_<ARM> as for run.py. Per task and arm: the parent's tree with no
history, the generated Cargo.lock when the repository has none, and a target dir cloned
from the repo's validation target with the workspace's own crates cleaned and rebuilt at
the parent — so the dependencies are warm and nothing of the fixed commit is left in
it. The shared CARGO_HOME is filled at prep time and only read by the agent, whose Bash
is sandboxed with no network and builds with CARGO_NET_OFFLINE. Scored as score.py does,
with Rust's production rule, Rust's escape patterns outside inline tests, and the hidden
tests run by cargo per test target.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cargo   # noqa: E402
import draw    # noqa: E402
import run     # noqa: E402
import score   # noqa: E402
mine = cargo.mine

PROMPT = """You are working in a Rust repository. Implement the change described below.

The dependencies are fetched and cargo works offline; run the tests with `cargo test`. Do not use the network. When the change is done, stop.

--- task ---
{text}
"""

ALLOWED = ["Read", "Glob", "Grep", "Edit(./**)", "Write(./**)",
           "Bash(cargo test)", "Bash(cargo test *)", "Bash(cargo check)", "Bash(cargo check *)",
           "Bash(cargo build)", "Bash(cargo build *)", "Bash(cargo run *)", "Bash(cargo clippy *)", "Bash(cargo fmt *)",
           "Bash(git status *)", "Bash(git diff *)", "Bash(git log *)", "Bash(git show *)",
           "Bash(ls *)", "Bash(head *)", "Bash(tail *)", "Bash(wc *)",
           "Bash(python3 quality/bin/*)"]   # cleat's own gates, which its instructions tell the agent to run


def clean_env():
    env = _clean_env()
    env.update(CARGO_HOME=cargo.CARGO_HOME, CARGO_NET_OFFLINE="true", CARGO_TERM_COLOR="never")
    return env


def sandbox(clones, work, root=None):
    # run.py's sandbox, with the validation targets — built from the fixed commit — unreadable too
    settings = run.json.loads(_sandbox(clones, work, root))
    settings["sandbox"]["filesystem"]["denyRead"].append(cargo.TARGETS)
    settings["permissions"]["deny"] += ["%s(/%s/**)" % (tool, cargo.TARGETS) for tool in ("Read", "Grep", "Glob", "Edit", "Write")]
    # test suites that make their scratch dirs under the user cache dir (just's do), as
    # Python's make theirs under TMPDIR, which the sandbox already allows
    settings["sandbox"]["filesystem"]["allowWrite"] = [os.path.expanduser("~/Library/Caches")]
    return run.json.dumps(settings)


def install_recipe(root, recipe, repo=None):
    """What draw.install_recipe is to a venv: the lock, and a warm target dir at the parent."""
    cargo.apply_recipe(root, recipe)
    with open(os.path.join(root, ".gitignore"), "a") as handle:
        handle.write("/target/\n")
    if repo:
        target = cargo.seed_target(repo, root)
        meta = cargo.metadata(root) or {"packages": []}
        env = cargo.env(target)
        env["CARGO_NET_OFFLINE"] = "true"
        names = sorted({p["name"] for p in meta["packages"]})
        if names:
            subprocess.run(["cargo", "clean", "--offline"] + [a for n in names for a in ("-p", n)],
                           cwd=root, capture_output=True, env=env)
        # extract every crate any target needs into CARGO_HOME, which the agent cannot write,
        # and warm the parent's own build
        try:
            subprocess.run(["cargo", "test", "--no-run", "--offline", "--workspace"], cwd=root, capture_output=True,
                           env=env, timeout=cargo.TIMEOUT)
        except subprocess.TimeoutExpired:
            pass


def prepare(clones, task, arm, root):
    real = draw.install_recipe
    draw.install_recipe = lambda r, recipe: install_recipe(r, recipe, task["repo"])
    try:
        return _prepare(clones, task, arm, root)
    finally:
        draw.install_recipe = real


def production_tree(root):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("quality", "target")]
        for name in files:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if cargo.production(rel):
                yield rel


def hidden_tests(clones, task, root):
    draw.overlay(os.path.join(clones, task["repo"]), task["sha"], root, task["overlay"])
    try:
        outcome = cargo.run_tests(root, [tuple(t) for t in task["targets"]], os.path.join(root, "target"))
    except subprocess.TimeoutExpired:
        outcome = {}
    f2p = [t for t in task["f2p"] if outcome.get(t)]
    p2p = [t for t in task["p2p"] if outcome.get(t)]
    return dict(resolved=len(f2p) == len(task["f2p"]) and len(p2p) == len(task["p2p"]),
                f2p_passed=len(f2p), f2p=len(task["f2p"]), p2p_passed=len(p2p), p2p=len(task["p2p"]))


_clean_env, _sandbox, _prepare = run.clean_env, run.sandbox, run.prepare
run.PROMPT, run.ALLOWED, run.clean_env, run.sandbox, run.prepare = PROMPT, ALLOWED, clean_env, sandbox, prepare
mine.production = cargo.production
mine.escape_counts = lambda text: [len(p.findall(cargo.without_inline_tests(text))) for p in cargo.ESCAPES]
score.SKIP = score.SKIP + ("target/",)
score.production_tree, score.hidden_tests = production_tree, hidden_tests


if __name__ == "__main__":
    run.main(*sys.argv[1:])

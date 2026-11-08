#!/usr/bin/env python3
"""chain — one arm re-implementing a plan's changes in order, each on the tree the last one left.

  chain.py <clones dir> <work dir> <up to change> <chain …>        (chains named plain-1, cleat-2, …)

A chain starts from upstream's tree at the first change's parent, its dependencies installed,
committed as "base"; the cleat arm then attaches cleat from CLEAT_CLEAT (default the f6e08df
worktree at .study-work/cleat-head where it exists, else this checkout) and commits it; an attach
that fails stops the chain, which would otherwise run as a second plain arm. Per change: the tree is reset to its last
commit; every file that is neither production nor test (manifests, configs, docs) is brought
to upstream's state at the change, the commits between replayed changes included, and committed, and the dependencies reinstalled if a manifest
moved; the agent gets the change's text, with the paired runs' model, budget, flags and
parking, in a sandbox that hides the clones, the plan and every other chain; its work is
committed as "change <n>", so cleat judges each change against the one before, as on a
main branch. A change is accepted only as a project's CI would take it: on a copy of the tree,
its own test files and type-check (lang.suite) must show no compile error, failing test or
test that stopped running that the tree handed to the agent did not have. The agent gets
what broke and tries again, ATTEMPTS times in all; a change still failing is rejected, the
tree goes back to where the agent found it, and the change counts as unresolved. A scored change is then checked on an APFS clone of the tree with upstream's
files for it and its test-runner configs laid over: tests missing from the report (a compile or import failure) are
counted apart from tests that ran and failed; it is checked again with every upstream test
file laid over, helpers included. The tree is measured after every change and
copied at the plan's checkpoints for the probes. Each change writes
<work>/chains/<chain>/steps/<n>/score.json; a change with one is not run again. A chain named
oracle-… lays upstream's own production files on instead of running an agent: every scored
change should then resolve, or the harness, not an arm, is failing it; no other chain starts
until one has resolved 90% of the scored changes it would run to (`oracle_gate`). Its tree keeps the
base's tests, which upstream's code outgrows, so it is not held to the suite. The work dir must sit
outside this repository with no CLAUDE.md above it, and each change records any instructions
file or memory the agent left (`stray`).
"""

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lang as languages   # noqa: E402
import measure             # noqa: E402
import run                 # noqa: E402  (lang put the paired runs on the path)
import draw                # noqa: E402
import score               # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
WORK_ROOT = os.path.join(REPO, ".study-work")
INSTRUCTIONS = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")
PINNED = os.path.join(WORK_ROOT, "cleat-head")   # the study's own runs: cleat at f6e08df
CLEAT = os.environ.get("CLEAT_CLEAT") or (PINNED if os.path.isdir(PINNED) else REPO)
ATTEMPTS = 3
ORACLE_SHARE = 0.9   # the 2026-11-04 pre-registration's gate on the harness
ACCEPT = """

--- acceptance ---
The change is accepted only when the project builds and its whole test suite passes, with no test deleted or skipped."""
RETRY = """

--- your work so far is not accepted ---
These checks fail on the tree as you left it:

{problems}

The test runner's last lines:

{output}

Fix the code so the project builds and its whole test suite passes. Do not delete, skip or weaken tests to get there. When it passes, stop."""


def load(work):
    return json.load(open(os.path.join(work, "plan.json")))


def hint(plan):
    return next((c["hint"] for c in plan["changes"] if c.get("hint")), None) or "the project's test runner"


def recipe(plan):
    return next((c["recipe"] for c in plan["changes"] if c.get("recipe")), None)


def hidden(base, *keep):
    """Every path under `base` that is not on the way to one of `keep` or inside it."""
    out = []
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        if path in keep:
            continue
        if any(k.startswith(path + os.sep) for k in keep):
            out += hidden(path, *keep)
        else:
            out.append(path)
    return out


def guard(work):
    """The work dir sits outside this repository and under no instructions file: an agent
    loads every CLAUDE.md above its directory, and this repository's would set it rules."""
    if os.path.realpath(work).startswith(os.path.realpath(REPO) + os.sep):
        raise SystemExit("work dir %s is inside the cleat repository" % work)
    d = os.path.dirname(os.path.realpath(work))
    while True:
        found = [n for n in INSTRUCTIONS if os.path.exists(os.path.join(d, n))]
        if found:
            raise SystemExit("an agent under %s would load %s" % (work, os.path.join(d, found[0])))
        if d == os.path.dirname(d):
            return
        d = os.path.dirname(d)


def oracle_resolved(work, chain, scored):
    """How many of the scored changes an oracle chain resolved by the full check (analyze.full)."""
    count = 0
    for change in scored:
        path = os.path.join(work, "chains", chain, "steps", "%03d" % change["index"], "score.json")
        if os.path.isfile(path):
            with open(path) as handle:
                count += bool((json.load(handle).get("check_full") or {}).get("resolved"))
    return count


def oracle_gate(work, upto, chains):
    """No agent chain starts until an oracle chain has resolved at least ORACLE_SHARE of the scored
    changes it would run to: below that the harness or the language's adapter is failing upstream's
    own code, and every agent run on top of it would be spent on numbers that mean nothing."""
    agents = [c for c in chains if not c.startswith("oracle")]
    scored = [c for c in load(work)["changes"][:upto] if c.get("scored")]
    if not agents or not scored:
        return
    chains_dir = os.path.join(work, "chains")
    oracles = [c for c in sorted(os.listdir(chains_dir)) if c.startswith("oracle")] if os.path.isdir(chains_dir) else []
    best = max([oracle_resolved(work, c, scored) for c in oracles] or [0])
    if best < ORACLE_SHARE * len(scored):
        raise SystemExit("an oracle chain must resolve at least %d%% of the %d scored changes through change %d "
                         "before %s run; the best has %d. Run it alone first: chain.py <clones dir> %s %d oracle-1. "
                         "Where it falls short, its steps' check-full.log show what failed: fix the harness or the "
                         "adapter, not the arms." % (ORACLE_SHARE * 100, len(scored), upto, ", ".join(agents), best, work, upto))


def memories(root):
    """Files the agent's auto memory holds for its directory: a channel between changes."""
    slug = os.path.realpath(root).replace("/", "-").replace(".", "-")
    path = os.path.join(os.path.expanduser("~/.claude/projects"), slug, "memory")
    return sorted(os.listdir(path)) if os.path.isdir(path) else []


def sandbox(clones, work, own, lang=None):
    """This repository (the study's hypotheses, the clones) but for the language's tool dirs, and
    everything beside the run's own directory in the study's dir, are unreadable: the plans,
    every other chain and probe."""
    tools = [p for p in (getattr(lang, "tools", ()) if lang else ()) if p.startswith(REPO + os.sep)]
    paths = hidden(REPO, *tools) + hidden(os.path.dirname(work), own)
    deny = ["%s(/%s%s)" % (tool, p, "/**" if os.path.isdir(p) else "") for p in paths
            for tool in ("Read", "Grep", "Glob", "Edit", "Write")]
    settings = {"permissions": {"deny": deny},
                "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": False, "failIfUnavailable": True,
                            "filesystem": {"denyRead": paths}}}
    if lang:
        lang.sandbox_extra(settings)
    return json.dumps(settings)


def tree(clone, sha, target):
    """Upstream's files at a commit, stamped now: cargo judges freshness by mtime."""
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target)
    archive = subprocess.Popen(["git", "-C", clone, "archive", sha], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-m", "-x", "-C", target], stdin=archive.stdout, check=True)
    archive.wait()


def commit(root, message):
    run.git(root, "add", "-A")
    run.git(root, "commit", "-q", "--allow-empty", "-m", message)


def setup(lang, plan, clones, work, chain, root):
    clone = os.path.join(clones, plan["repo"])
    tree(clone, plan["changes"][0]["parent"], root)
    subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
    got, why = lang.install(root, recipe(plan), update=True) if lang.name == "rust" else lang.install(root, recipe(plan))
    with open(os.path.join(root, ".gitignore"), "a") as handle:
        handle.write("\n" + "\n".join(lang.ignored) + "\n")
    commit(root, "base")
    info = dict(install=why, recipe=got)
    if chain.split("-")[0] == "cleat":   # the oracle and plain arms get nothing
        info["cleat"] = subprocess.run(["git", "-C", CLEAT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        out = subprocess.run(["python3", os.path.join(CLEAT, "quality", "bin", "attach.py"), "--into", root],
                             capture_output=True, text=True, env=run.clean_env())
        info["attach"] = out.stdout[-3000:] + out.stderr[-1000:]
        if out.returncode != 0:
            raise SystemExit("%s: cleat's attach failed, so this chain would run without it:\n%s" % (chain, info["attach"][-1500:]))
        commit(root, "attach cleat")
        gate = subprocess.run(["python3", "quality/bin/gate.py"], cwd=root, capture_output=True, text=True, env=run.clean_env())
        info["day_one_gate"] = gate.returncode
    info["measure"] = measure.measure(lang.name, root)
    return info


def attached(root):
    """The files cleat's attach wrote, which upstream's edits must not undo."""
    sha = next((line.split()[0] for line in score.out(root, "log", "--format=%H %s").splitlines()
                if line.split(" ", 1)[1:] == ["attach cleat"]), None)
    return set(score.out(root, "show", "--name-only", "--format=", sha).split()) if sha else set()


def upstream_edits(lang, clone, base, change, root):
    """Bring every file that is neither production nor test to upstream's state at the change:
    everything that moved since `base`, the commits between replayed changes included (a
    dependency bump touches no production file, so the stretch never replays it), but for
    cleat's own files and the language's kept ones; whether a manifest moved."""
    own = attached(root)
    moved = score.out(clone, "diff", "--name-only", "--no-renames", base, change["sha"]).split("\n")
    paths = [p for p in moved if p and not lang.production(p) and not lang.is_test(p)
             and not score.mine.summary._is_test(p) and os.path.basename(p) not in lang.keep_own
             and p not in own and not p.startswith(("quality/", ".claude/"))]
    draw.overlay(clone, change["sha"], root, paths)
    if ".gitignore" in paths:   # upstream's own, with what the replay keeps out of every commit
        with open(os.path.join(root, ".gitignore"), "a") as handle:
            handle.write("\n" + "\n".join(lang.ignored) + "\n")
    return any(os.path.basename(p) in lang.manifests for p in paths), paths


def previous(plan, change):
    """The commit a change's tree stands on upstream: the replayed change before it, or the stretch's parent."""
    i = next(n for n, c in enumerate(plan["changes"]) if c["index"] == change["index"])
    return plan["changes"][i - 1]["sha"] if i else plan["changes"][0]["parent"]


def agent(lang, plan, change, clones, work, own, root, out_dir, rec, retry=None):
    prompt = lang.prompt.format(hint=hint(plan), text=change["text"] + ACCEPT) + (RETRY.format(**retry) if retry else "")
    cmd = ["claude", "-p", prompt, "--model", run.MODEL, "--setting-sources", "project",
           "--settings", sandbox(clones, work, own, lang), "--strict-mcp-config", "--permission-mode", "dontAsk",
           "--allowedTools", ",".join(lang.allowed), "--disallowedTools", "WebFetch,WebSearch,Agent",
           "--max-budget-usd", run.BUDGET, "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    with open(os.path.join(out_dir, "transcript.jsonl"), "w") as handle:
        try:
            p = subprocess.run(cmd, cwd=root, stdout=handle, stderr=subprocess.PIPE, text=True,
                               timeout=run.TIMEOUT, env=lang.env(rec))
            return dict(exit=p.returncode, stderr=p.stderr[-2000:])
        except subprocess.TimeoutExpired:
            return dict(exit="timeout")


def upstream_tests(lang, clone, sha):
    """Every test file of upstream's tree at a commit, its helpers included."""
    paths = score.out(clone, "ls-tree", "-r", "--name-only", sha).split("\n")
    return [p for p in paths if p and not lang.production(p) and (lang.is_test(p) or score.mine.summary._is_test(p))]


def runner_configs(lang, clone, sha):
    """Upstream's test-runner configs at a commit, which its tests need to load."""
    return [p for p in score.out(clone, "ls-tree", "-r", "--name-only", sha).split("\n") if p and lang.runner_config.search(p)]


def check(lang, clone, change, root, out_dir, full=False):
    """Upstream's tests for the change over an APFS clone of the tree: the change's own files
    laid over, as pre-registered, or with `full` every test file of upstream's tree at the
    change, so an arm that reshaped a shared test helper is judged on its code, not the helper."""
    copy = os.path.join(out_dir, "check")
    shutil.rmtree(copy, ignore_errors=True)
    draw.copy_tree(root, copy)
    try:
        laid = change["overlay"] + runner_configs(lang, clone, change["sha"])
        laid += upstream_tests(lang, clone, change["sha"]) if full else []
        draw.overlay(clone, change["sha"], copy, laid)
        lang.before_check(copy)
        outcome = lang.run_tests(copy, change, "hidden")
        with open(os.path.join(out_dir, "check%s.log" % ("-full" if full else "")), "w") as handle:
            handle.write("\n".join(lang.output()))
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    wanted = change["f2p"] + change["p2p"]
    return dict(resolved=all(outcome.get(t) for t in wanted),
                f2p_passed=sum(bool(outcome.get(t)) for t in change["f2p"]), f2p=len(change["f2p"]),
                p2p_passed=sum(bool(outcome.get(t)) for t in change["p2p"]), p2p=len(change["p2p"]),
                missing=sum(t not in outcome for t in wanted), failed=sum(outcome.get(t) is False for t in wanted),
                tests={t: outcome.get(t) for t in wanted})


def suite(lang, root, out_dir):
    """lang.suite on an APFS clone of the tree, its runner's output kept beside it."""
    copy = os.path.join(out_dir, "suite")
    shutil.rmtree(copy, ignore_errors=True)
    draw.copy_tree(root, copy)
    try:
        lang.before_check(copy)
        got = lang.suite(copy)
        got["output"] = lang.output()
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    with open(os.path.join(out_dir, "suite.log"), "w") as handle:
        handle.write("\n".join(got["output"]))
    return got


def failures(before, after):
    """What the agent's work breaks against the tree it was handed: a compile error it adds, a test
    that fails and did not, a test that passed and no longer runs (deleted, skipped, or in a file
    that stopped loading), and a test file that reports no test."""
    out = ["compile error: " + e for e in after["errors"] if e not in set(before["errors"])]
    out += ["test fails: " + t for t, ok in sorted(after["tests"].items()) if not ok and before["tests"].get(t) is not False]
    out += ["test no longer runs: " + t for t, ok in sorted(before["tests"].items()) if ok and t not in after["tests"]]
    out += ["test file runs no test: " + f for f in after["silent"] if f not in set(before["silent"])]
    return out


def accepted(lang, plan, change, clones, work, chain, root, out_dir, rec, info):
    """The agent's work, sent back with what broke until it passes the tree's own suite or
    ATTEMPTS runs are spent: True, False, or None when the usage limit parked it."""
    before = suite(lang, root, out_dir)
    info["suite_before"] = dict(tests=len(before["tests"]), failing=sum(not ok for ok in before["tests"].values()),
                                errors=len(before["errors"]), silent=len(before["silent"]))
    info["attempts"], retry = [], None
    for attempt in range(1, ATTEMPTS + 1):
        run_dir = out_dir if attempt == 1 else os.path.join(out_dir, "attempt-%d" % attempt)
        os.makedirs(run_dir, exist_ok=True)
        result = agent(lang, plan, change, clones, work, os.path.join(work, "chains", chain), root, run_dir, rec, retry)
        if run.limited(run_dir, result):
            return None
        problems = failures(before, suite(lang, root, run_dir))
        info["attempts"].append(dict(agent=result, run=score.run_result(run_dir), problems=problems[:200],
                                     broken=len(problems)))
        if not problems:
            return True
        retry = dict(problems="\n".join(problems[:40]) + ("\n… and %d more" % (len(problems) - 40) if len(problems) > 40 else ""),
                     output="\n".join(suite_tail(run_dir)))
    return False


def suite_tail(run_dir, lines=60):
    with open(os.path.join(run_dir, "suite.log"), errors="replace") as handle:
        return handle.read().split("\n")[-lines:]


def total(runs):
    """The attempts' runs as one: what the change cost in all."""
    out = dict(runs[0]) if runs else {}
    for k in ("total_cost_usd", "num_turns", "duration_ms"):
        if any(r.get(k) is not None for r in runs):
            out[k] = sum(r.get(k) or 0 for r in runs)
    return out


def step(lang, plan, clones, work, chain, root, change, rec):
    out_dir = os.path.join(work, "chains", chain, "steps", "%03d" % change["index"])
    if os.path.isfile(os.path.join(out_dir, "score.json")):
        return "cached", rec
    if run.PARKED.is_set():
        return "skipped: usage limit", rec
    clone = os.path.join(clones, plan["repo"])
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)
    run.git(root, "reset", "-q", "--hard")
    run.git(root, "clean", "-q", "-fd")
    moved, laid = upstream_edits(lang, clone, previous(plan, change), change, root)
    info = dict(upstream_files=laid)
    if moved:
        rec, info["reinstall"] = lang.install(root, rec)
    commit(root, "upstream %d" % change["index"])
    if chain.startswith("oracle"):   # upstream's own production code: what the checks must pass
        draw.overlay(clone, change["sha"], root, [p for _, _, p in change["files"] if lang.production(p)])
        info["agent"] = "oracle"
    else:
        info["accepted"] = accepted(lang, plan, change, clones, work, chain, root, out_dir, rec, info)
        if info["accepted"] is None:
            run.PARKED.set()
            return "parked: usage limit, run again after it resets", rec
        info["agent"] = info["attempts"][0]["agent"]
        info["run"] = total([a["run"] for a in info["attempts"]])
        if not info["accepted"]:   # rejected: the tree goes back to where the agent found it
            run.git(root, "reset", "-q", "--hard")
            run.git(root, "clean", "-q", "-fd")
    commit(root, "change %d" % change["index"])
    info["files"] = score.out(root, "diff", "--name-only", "HEAD~1", "HEAD").split()
    info["stray"] = [p for p in info["files"] if os.path.basename(p) in INSTRUCTIONS + ("DECISIONS.md",)] + memories(root)
    if change["scored"]:
        info["check"] = check(lang, clone, change, root, out_dir)
        info["check_full"] = check(lang, clone, change, root, out_dir, full=True)
    info["measure"] = measure.measure(lang.name, root)
    with open(os.path.join(out_dir, "score.json"), "w") as handle:
        json.dump(info, handle, indent=1)
    checkpoints = [int(k) for k in plan["probes"]]
    if change["index"] in checkpoints:
        snap = os.path.join(work, "chains", chain, "snapshots", str(change["index"]))
        shutil.rmtree(snap, ignore_errors=True)
        os.makedirs(os.path.dirname(snap), exist_ok=True)
        draw.copy_tree(root, snap)
    tried = " tries=%d accepted=%s" % (len(info["attempts"]), info["accepted"]) if "attempts" in info else ""
    return (("resolved=%s" % info["check"]["resolved"]) if change["scored"] else "done") + tried, rec


def one_chain(clones, work, chain, upto):
    plan = load(work)
    lang = languages.get(plan["lang"])
    base = os.path.join(work, "chains", chain)
    root = os.path.join(base, "repo")
    if not os.path.isfile(os.path.join(base, "setup.json")):
        shutil.rmtree(base, ignore_errors=True)
        os.makedirs(base)
        info = setup(lang, plan, clones, work, chain, root)
        with open(os.path.join(base, "setup.json"), "w") as handle:
            json.dump(info, handle, indent=1)
    rec = json.load(open(os.path.join(base, "setup.json")))["recipe"]
    for change in plan["changes"][:upto]:
        outcome, rec = step(lang, plan, clones, work, chain, root, change, rec)
        print(chain, change["index"], change["sha"][:8], outcome, flush=True)
        if outcome.startswith(("parked", "skipped")):
            break


def main(clones, work, upto, *chains):
    clones, work = os.path.abspath(clones), os.path.abspath(work)
    guard(work)
    oracle_gate(work, int(upto), chains)
    for chain in chains:   # every chain's directory exists before any agent starts, so each is denied the others
        os.makedirs(os.path.join(work, "chains", chain), exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(len(chains)) as pool:
        list(pool.map(lambda c: one_chain(clones, work, c, int(upto)), chains))


if __name__ == "__main__":
    main(*sys.argv[1:])

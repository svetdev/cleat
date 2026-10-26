#!/usr/bin/env python3
"""score — what one agent run did, by the measures pre-registered on 2026-10-25.

On the agent's production diff: the complexity, escapes and duplication verdicts
as the JIT miner reads them (study/jit/mine.py), the largest cyclomatic among the
functions it changed or added, functions added, lines changed. Then cleat's real
gates on a fresh attach of the base with the agent's files copied in — the same
judge for both arms. Then the hidden tests: the commit's own test files over the
agent's tree, resolved when every fail-to-pass and pass-to-pass test passes.
Cost and turns come from the run's stream-json result.
"""

import collections
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLEAT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "jit"))
sys.path.insert(0, HERE)
import mine                                  # noqa: E402
from extractors import duplication            # noqa: E402  (mine put quality/bin on the path)
import lizard                                # noqa: E402
import draw                                  # noqa: E402

SKIP = (".venv/", "quality/", ".claude/", ".junit-")
AGENT_DOCS = ("CLAUDE.md", "AGENTS.md")


def out(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True).stdout


def start_commit(root):
    attach = out(root, "log", "--format=%H", "--grep", "^attach cleat$").split()
    return attach[0] if attach else out(root, "rev-list", "--max-parents=0", "HEAD").split()[0]


def changed_files(root, start):
    names = set()
    for line in out(root, "diff", "--name-only", start).splitlines():
        names.add(line)
    for line in out(root, "ls-files", "--others", "--exclude-standard").splitlines():
        names.add(line)
    return sorted(n for n in names if not n.startswith(SKIP) and n not in AGENT_DOCS)


def functions(text, path):
    try:
        return [(f.name, f.cyclomatic_complexity, f.length)
                for f in lizard.analyze_file.analyze_source_code(path, text).function_list]
    except Exception:
        return []


def touched(before, after):
    """The after-side functions that are new or differ from their namesake (paired in order)."""
    old = collections.defaultdict(list)
    for name, cc, length in before:
        old[name].append((cc, length))
    out_ = []
    for name, cc, length in after:
        match = old[name].pop(0) if old[name] else None
        if match != (cc, length):
            out_.append((name, cc, length))
    return out_


def windows(text):
    lines = duplication.significant(text)
    return [(hash("\n".join(t for _, t in lines[i:i + mine.DUP])), tuple(n for n, _ in lines[i:i + mine.DUP]))
            for i in range(len(lines) - mine.DUP + 1)]


def production_tree(root):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "quality"]
        for name in files:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if mine.production(rel):
                yield rel


def mined(root, start, files):
    prods = [f for f in files if mine.production(f)]
    index = collections.Counter()
    for rel in production_tree(root):
        with open(os.path.join(root, rel), errors="replace") as handle:
            index.update(h for h, _ in windows(handle.read()))
    result = dict(complexity=False, escapes=False, duplication=False, max_cc_changed=0, functions_added=0,
                  la=0, ld=0, production_files=len(prods))
    for rel in prods:
        before = out(root, "show", "%s:%s" % (start, rel))
        path = os.path.join(root, rel)
        after = open(path, errors="replace").read() if os.path.isfile(path) else ""
        fb, fa = functions(before, rel), functions(after, rel)
        result["complexity"] |= mine.complexity_fails(fb, fa)
        result["escapes"] |= any(b > a for a, b in zip(mine.escape_counts(before), mine.escape_counts(after)))
        result["max_cc_changed"] = max([result["max_cc_changed"]] + [cc for _, cc, _ in touched(fb, fa)])
        result["functions_added"] += len(fa) - len(fb)
        added = mine.added_lines(before, after)
        result["la"] += len(added)
        result["ld"] += len(mine.deleted_lines(before, after))
        wins = windows(after)
        positions = collections.defaultdict(list)
        for i, (h, _) in enumerate(wins):
            positions[h].append(i)
        for i, (h, numbers) in enumerate(wins):
            if added.intersection(numbers) and (index[h] > len(positions[h])
                                                or any(abs(i - j) >= mine.DUP for j in positions[h])):
                result["duplication"] = True
                break
    return result


def real_gate(clones, task, root, files, out_dir):
    """cleat's gates on a fresh attach of the base, with the agent's files copied in."""
    judge = os.path.join(out_dir, "judge")
    draw.tree(os.path.join(clones, task["repo"]), task["parent"], judge)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=s", "-c", "user.email=s@example.invalid", "commit", "-q", "-m", "base"]):
        subprocess.run(["git", "-C", judge, *args], capture_output=True)
    subprocess.run(["python3", os.path.join(CLEAT, "quality", "bin", "attach.py"), "--into", judge], capture_output=True, env=env)
    for args in (["add", "-A"], ["-c", "user.name=s", "-c", "user.email=s@example.invalid", "commit", "-q", "-m", "attach"]):
        subprocess.run(["git", "-C", judge, *args], capture_output=True)
    for rel in files:
        source, target = os.path.join(root, rel), os.path.join(judge, rel)
        if os.path.isfile(source):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(source, target)
        elif os.path.exists(target):
            os.remove(target)
    p = subprocess.run(["python3", "quality/bin/gate.py"], cwd=judge, capture_output=True, text=True, env=env)
    failing = sorted({line.split()[1] for line in p.stdout.splitlines() if line.split()[:1] == ["FAIL"] and len(line.split()) > 1})
    shutil.rmtree(judge, ignore_errors=True)
    return dict(exit=p.returncode, failing=failing, tail=p.stdout[-1500:])


def run_result(out_dir):
    result = {}
    path = os.path.join(out_dir, "transcript.jsonl")
    if not os.path.isfile(path):
        return result
    stop_blocks = 0
    for line in open(path, errors="replace"):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "result":
            result = {k: event.get(k) for k in ("subtype", "is_error", "num_turns", "total_cost_usd", "duration_ms")}
        if "Stop hook" in line or "stop_hook" in line:
            stop_blocks += 1
    result["stop_hook_mentions"] = stop_blocks
    return result


def hidden_tests(clones, task, root):
    draw.overlay(os.path.join(clones, task["repo"]), task["sha"], root, task["overlay"])
    outcome = draw.pytest(root, task["tests"], "hidden")
    f2p = [t for t in task["f2p"] if outcome.get(t)]
    p2p = [t for t in task["p2p"] if outcome.get(t)]
    return dict(resolved=len(f2p) == len(task["f2p"]) and len(p2p) == len(task["p2p"]),
                f2p_passed=len(f2p), f2p=len(task["f2p"]), p2p_passed=len(p2p), p2p=len(task["p2p"]))


def score(clones, task, arm, root, out_dir):
    start = start_commit(root)
    files = changed_files(root, start)
    result = dict(files=files, mined=mined(root, start, files), run=run_result(out_dir))
    events = os.path.join(root, "quality", ".events.jsonl")
    if os.path.isfile(events):
        result["cleat_events"] = [json.loads(l) for l in open(events) if l.strip()][-50:]
    result["real_gate"] = real_gate(clones, task, root, files, out_dir)
    result.update(hidden_tests(clones, task, root))
    return result

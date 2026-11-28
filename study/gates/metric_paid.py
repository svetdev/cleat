#!/usr/bin/env python3
"""metric_paid — how an agent answers a complexity refusal: with simpler code, or with the number.

  metric_paid.py <out.json> <paired work dir> [<paired work dir> …]
  metric_paid.py <out.json> show <work dir> <task> <arm>   what one run did after its first refusal

Every run of an arm other than `plain` whose transcript shows the complexity gate failing in a
tool result. HEAD of the run's repo is the tree the agent was handed and the working tree is
what it left. A helper is a function defined in a production file the agent changed that HEAD
does not define anywhere and that no edit made before the first refusal defines: it came after
the refusal. Its uses are the places its name stands as a word in the production files outside
its own `def` line, which is how the gate itself tells a single-use helper.

Four classes are mechanical. `single-use` when every such helper is named in one place and
`shared` when one is named in two or more. With no helper, the functions the refusal named are
read as the agent left them against HEAD's, or against the refused draft in the agent's own
edits when HEAD has none: `disguised` when the function gained a decision in a form the count
does not see (a dict literal's `.get`, `filter`, `map`, a fold such as `any` over something
other than a comprehension, `|=`, a `getattr` default, a `lambda`), and `simpler` when it
gained none and holds no more `if`, loop, `except`, comprehension and boolean operators than
before. What is left is read by eye, with
`show`, and written to <out.json>.eye.json as {"<work dir> <task> <arm>": "<class>"}: `dropped` (behaviour
the task asked for left out), `other`, or one of the classes above. Starts no agent and writes
only <out.json>.
"""

import ast
import collections
import json
import os
import re
import subprocess
import sys
import textwrap

FAIL = "  FAIL  complexity"
NAMED = re.compile(r"^\s+(\S+?):\d+\s+cc \d+.*?\bdef (\w+)", re.M)
DEF = re.compile(r"^\s*(?:async\s+)?def (\w+)", re.M)
TEST = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$|_test\.py$|conftest\.py$")
VAGUE = re.compile(r"(_part|_step|_helper|_impl|_inner|_rest|_body|_continued|\d)$")
FOLDS = ("any", "all", "next", "max", "min")
CLASSES = ("single-use", "shared", "disguised", "simpler", "dropped", "other", "unread")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, errors="replace").stdout


def text_of(content):
    if isinstance(content, str):
        return content
    return " ".join(part.get("text", "") for part in content or [] if isinstance(part, dict))


def written(block):
    """The text an Edit, MultiEdit or Write call puts in a file, and the text it replaces."""
    given = block.get("input") or {}
    edits = given.get("edits") or [given]
    new = "\n".join(str(e.get("new_string") or e.get("content") or "") for e in edits)
    old = "\n".join(str(e.get("old_string") or "") for e in edits)
    return given.get("file_path") or "", old, new


def blocks(path):
    with open(path, errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            message = event.get("message") if isinstance(event, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict):
                    yield event.get("type"), block


def events(path):
    """The run in order: ("refusal", text), ("edit", (file, old, new)), ("said", text)."""
    for kind, block in blocks(path):
        if block.get("type") == "tool_result" and FAIL in text_of(block.get("content")):
            yield "refusal", text_of(block.get("content"))
        elif block.get("type") == "tool_use" and block.get("name") in ("Edit", "MultiEdit", "Write"):
            yield "edit", written(block)
        elif kind == "assistant" and block.get("type") == "text" and block["text"].strip():
            yield "said", block["text"].strip()


def refused(text):
    """The (file, function) pairs a refusal names under the complexity gate."""
    block = text.split(FAIL, 1)[1].split("\n  ok  ")[0].split("\n  FAIL  ")[0]
    return sorted(set(NAMED.findall(block.split("NOTE:")[0])))


def changed(root):
    """Production Python files the agent left different from HEAD, cleat's own left out."""
    listed = git(root, "status", "--porcelain", "--untracked-files=all").splitlines()
    paths = [line[3:].split(" -> ")[-1].strip('"') for line in listed]
    return [p for p in paths if p.endswith(".py") and not TEST.search(p) and not p.startswith("quality/")]


def production(root):
    listed = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "*.py").splitlines()
    return [p for p in listed if not TEST.search(p) and not p.startswith("quality/")]


def read(root, path):
    try:
        with open(os.path.join(root, path), errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def uses(root, name, paths):
    """How many places name the function outside its own declaration: production, and tests."""
    word = re.compile(r"\b%s\b" % re.escape(name))
    own = re.compile(r"def %s\b" % re.escape(name))
    count = collections.Counter()
    for path in paths:
        body = read(root, path)
        count["test" if TEST.search(path) else "production"] += len(word.findall(body)) - len(own.findall(body))
    return count["production"], count["test"]


def helpers(root, before):
    """{name: (production uses, test uses)} of the functions that came after the first refusal."""
    head = set(re.findall(r"def (\w+)", git(root, "grep", "-hoE", "def [A-Za-z0-9_]+", "HEAD", "--", "*.py")))
    early = set(DEF.findall("\n".join(new for _, _, new in before)))
    final = set(DEF.findall("\n".join(read(root, p) for p in changed(root))))
    paths = production(root) + [p for p in git(root, "ls-files", "--others", "--exclude-standard", "*.py").splitlines()
                                if TEST.search(p)]
    return {name: uses(root, name, paths) for name in sorted(final - head - early)}


def function(source, name):
    """The first function of that name in a piece of Python, as a syntax tree, or None."""
    try:
        tree = ast.parse(textwrap.dedent(source))
    except (SyntaxError, ValueError):
        return None
    return next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)


def unseen(node):
    """Whether a node decides something in a form the cyclomatic count passes over."""
    if isinstance(node, ast.Lambda):
        return True
    if isinstance(node, ast.AugAssign):
        return isinstance(node.op, (ast.BitOr, ast.BitAnd))
    if not isinstance(node, ast.Call):
        return False
    called = node.func
    if isinstance(called, ast.Attribute):
        return called.attr == "get" and isinstance(called.value, ast.Dict)
    name = getattr(called, "id", "")
    whole = bool(node.args) and not isinstance(node.args[0], (ast.GeneratorExp, ast.ListComp))
    return name in ("filter", "map") or (name in FOLDS and whole) or (name == "getattr" and len(node.args) == 3)


def seen(node):
    """How many decisions the count sees at a node."""
    if isinstance(node, ast.BoolOp):
        return len(node.values) - 1
    if isinstance(node, ast.comprehension):
        return 1 + len(node.ifs)
    return int(isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp)))


def counts(tree):
    nodes = list(ast.walk(tree))
    return sum(unseen(n) for n in nodes), sum(seen(n) for n in nodes)


def shape(root, path, name, before):
    """(unseen, seen) forms of a named function before and after, or None where one is missing."""
    after = function(read(root, path), name)
    was = function(git(root, "show", "HEAD:" + path), name)
    if was is None:
        was = next(filter(None, (function(new, name) for _, _, new in reversed(before))), None)
    return None if after is None or was is None else counts(was) + counts(after)


def mechanical(found, shapes):
    if found:
        return "shared" if any(production_uses >= 2 for production_uses, _ in found.values()) else "single-use"
    known = [s for s in shapes if s]
    if not known or len(known) < len(shapes):
        return None
    if any(unseen_after > unseen_was for unseen_was, _, unseen_after, _ in known):
        return "disguised"
    return "simpler" if all(seen_after <= seen_was for _, seen_was, _, seen_after in known) else None


def one(work, task, arm):
    base = os.path.join(work, "runs", task, arm)
    run = list(events(os.path.join(base, "transcript.jsonl")))
    first = next((i for i, (kind, _) in enumerate(run) if kind == "refusal"), None)
    if first is None:
        return None
    with open(os.path.join(base, "score.json")) as handle:
        score = json.load(handle).get("score") or {}
    root, before = os.path.join(base, "repo"), [e for kind, e in run[:first] if kind == "edit"]
    found, named = helpers(root, before), refused(run[first][1])
    shapes = [shape(root, path, name, before) for path, name in named]
    return dict(work=os.path.basename(work.rstrip("/")), task=task, arm=arm, resolved=bool(score.get("resolved")),
                refusals=sum(kind == "refusal" for kind, _ in run), named=named, helpers=found, shapes=shapes,
                mechanical=mechanical(found, shapes))


def runs(work):
    root = os.path.join(work, "runs")
    for task in sorted(os.listdir(root)):
        for arm in sorted(os.listdir(os.path.join(root, task))):
            done = os.path.isfile(os.path.join(root, task, arm, "score.json"))
            if done and arm.split("-")[0] not in ("plain", "probe"):
                yield task, arm


def measure(works):
    rows = []
    for work in works:
        for task, arm in runs(work):
            row = one(work, task, arm)
            if row:
                rows.append(row)
    return rows


def classed(row, eye):
    return eye.get("%s %s %s" % (row["work"], row["task"], row["arm"])) or row["mechanical"] or "unread"


def tally(rows, eye):
    lines = []
    for label in CLASSES:
        mine = [r for r in rows if classed(r, eye) == label]
        if mine:
            lines.append("  %-10s %3d runs (%2d%%), %2d tasks, resolved %2d" % (
                label, len(mine), round(100 * len(mine) / len(rows)), len({r["task"] for r in mine}),
                sum(r["resolved"] for r in mine)))
    return lines


def helper_lines(rows):
    names = [(name, counts) for r in rows for name, counts in r["helpers"].items()]
    single = [name for name, (production_uses, _) in names if production_uses == 1]
    return ["helpers that came after a refusal: %d, named in one production place %d, in two or more %d, in none %d"
            % (len(names), len(single), sum(c[0] >= 2 for _, c in names), sum(c[0] == 0 for _, c in names)),
            "single-use helpers with a numbered or part-style name: %d of %d; also named in a test: %d"
            % (sum(bool(VAGUE.search(n)) for n in single), len(single),
               sum(c[0] == 1 and c[1] > 0 for _, c in names))]


def report(rows, eye):
    print("%d runs where the complexity gate refused, %d tasks" % (len(rows), len({r["task"] for r in rows})))
    print("\n".join(tally(rows, eye)))
    for work in sorted({r["work"] for r in rows}):
        print(work)
        print("\n".join("  " + line for line in tally([r for r in rows if r["work"] == work], eye)))
    print("\n".join(helper_lines(rows)))


def show(rows, works, name, task, arm):
    """One run after its first refusal: what was named, what the agent said, what it edited."""
    row = next(r for r in rows if (r["work"], r["task"], r["arm"]) == (name, task, arm))
    work = next(w for w in works if os.path.basename(w.rstrip("/")) == name)
    run = list(events(os.path.join(work, "runs", task, arm, "transcript.jsonl")))
    first = next(i for i, (kind, _) in enumerate(run) if kind == "refusal")
    print(json.dumps({k: row[k] for k in ("named", "helpers", "shapes", "resolved", "refusals")}))
    for kind, what in run[first + 1:]:
        if kind == "said":
            print("SAID", what.replace("\n", " ")[:500])
        elif kind == "edit" and what[0].endswith(".py") and not TEST.search(what[0]):
            print("EDIT %s\n--- old\n%s\n+++ new\n%s" % (os.path.basename(what[0]), what[1][:1500], what[2][:1500]))


def main(out, *rest):
    eye_path = out + ".eye.json"
    eye = json.load(open(eye_path)) if os.path.isfile(eye_path) else {}
    if rest and rest[0] == "show":
        saved = json.load(open(out))
        return show(saved["rows"], saved["works"], *rest[1:4])
    works = [os.path.expanduser(w) for w in rest]
    rows = measure(works)
    with open(out, "w") as handle:
        json.dump(dict(works=works, rows=rows), handle, indent=1)
    report(rows, eye)


if __name__ == "__main__":
    main(*sys.argv[1:])

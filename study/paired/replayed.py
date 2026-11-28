#!/usr/bin/env python3
"""replayed — a file as the agent left it, rebuilt from the run's transcript.

score.py lays the commit's hidden test files over the run's tree, so the agent's own version
of those files is gone from disk. This replays the run's Write and Edit calls on the file as
the base commit has it. `python3 replayed.py <work dir>` checks the method on files that were
not overwritten: every other file the agent changed is rebuilt and compared with the tree.
"""

import glob
import json
import os
import subprocess
import sys


def calls(transcript):
    """The Write and Edit calls that did not fail, in order: (tool, input)."""
    used, failed = [], set()
    for line in open(transcript, errors="replace"):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        message = event.get("message") if isinstance(event, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        for part in (p for p in (content if isinstance(content, list) else []) if isinstance(p, dict)):
            if part.get("type") == "tool_use" and part.get("name") in ("Write", "Edit"):
                used.append((part["id"], part["name"], part.get("input") or {}))
            elif part.get("type") == "tool_result" and part.get("is_error"):
                failed.add(part.get("tool_use_id"))
    return [(name, given) for call, name, given in used if call not in failed]


def applied(text, name, given):
    if name == "Write":
        return given.get("content", "")
    old, new = given.get("old_string", ""), given.get("new_string", "")
    if text is None or not old or old not in text:
        return text
    return text.replace(old, new) if given.get("replace_all") else text.replace(old, new, 1)


def rebuilt(root, transcript, paths):
    """{path: text or None} for the repo-relative paths, as the agent's calls left them."""
    real = os.path.realpath(root)
    out = {}
    for path in paths:
        shown = subprocess.run(["git", "-C", root, "show", "HEAD:" + path], capture_output=True, text=True, errors="replace")
        out[path] = shown.stdout if shown.returncode == 0 else None
    for name, given in calls(transcript):
        target = given.get("file_path") or ""
        target = os.path.realpath(target if os.path.isabs(target) else os.path.join(root, target))
        relative = os.path.relpath(target, real)
        if relative in out:
            out[relative] = applied(out[relative], name, given)
    return out


def changed_names(root, hidden=()):
    names = subprocess.run(["git", "-C", root, "diff", "HEAD", "--name-only"], capture_output=True, text=True).stdout.split()
    names += subprocess.run(["git", "-C", root, "ls-files", "--others", "--exclude-standard"], capture_output=True, text=True).stdout.split()
    return [n for n in names if n.endswith(".py") and n not in hidden and not n.startswith(("quality/", ".claude/"))]


def faithful(root, transcript, hidden):
    """Whether every changed file the hidden tests did not overwrite rebuilds to what is on disk.
    An agent that wrote a file through a script leaves calls that do not; its run cannot be replayed."""
    for name, text in rebuilt(root, transcript, changed_names(root, hidden)).items():
        path = os.path.join(root, name)
        if text != (open(path, errors="replace").read() if os.path.isfile(path) else None):
            return False
    return True


def main(work):
    same = differ = 0
    for directory in sorted(glob.glob(os.path.join(work, "runs", "*", "*"))):
        root, transcript = os.path.join(directory, "repo"), os.path.join(directory, "transcript.jsonl")
        if not os.path.isfile(os.path.join(directory, "score.json")):
            continue
        task = os.path.basename(os.path.dirname(directory))
        tasks = {t["id"]: t for t in json.load(open(os.path.join(work, "tasks.json")))["tasks"]}
        hidden = set(tasks[task]["overlay"])
        names = changed_names(root, hidden)
        for name, text in rebuilt(root, transcript, names).items():
            actual = open(os.path.join(root, name), errors="replace").read() if os.path.isfile(os.path.join(root, name)) else None
            same += text == actual
            differ += text != actual
            if text != actual:
                print("differs:", directory, name)
    print("files rebuilt the same as the tree: %d, different: %d" % (same, differ))


if __name__ == "__main__":
    main(sys.argv[1])

#!/usr/bin/env python3
"""project_rules — the project's own lint and format, as a cleat `commands` gate that only tightens.

run.py copies this into a STUDY_RULES arm's tree as quality/project-rules.py and names it in
quality.json's `commands`, so the Stop hook runs it with the other gates. It judges what
gates/rules.py counts afterwards, by the same rule, on every Python file changed against HEAD:

  lint     where the project configures ruff: a violation `ruff check` reports in the file
           that it did not report for the file as HEAD has it.
  format   where the project's pre-commit names ruff-format or black: a file
           `ruff format --check` passed at HEAD and does not pass now.

Judged against HEAD, not outright, so a file that already broke a rule is not this change's
debt. Run with the tree's own interpreter (.venv/bin/python), which has ruff installed.
"""

import collections
import json
import os
import subprocess
import sys
import tempfile

CONFIGS = ("pyproject.toml", "ruff.toml", ".ruff.toml", "setup.cfg")


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, errors="replace")


def ruff(cwd, *args):
    return subprocess.run([sys.executable, "-m", "ruff", *args, "--no-cache"], cwd=cwd, capture_output=True, text=True)


def rules():
    """(lint is the project's rule, format is)."""
    texts = {c: open(c, errors="replace").read() for c in CONFIGS if os.path.isfile(c)}
    lints = "[tool.ruff" in texts.get("pyproject.toml", "") or any(c.endswith("ruff.toml") for c in texts)
    hooks = open(".pre-commit-config.yaml", errors="replace").read() if os.path.isfile(".pre-commit-config.yaml") else ""
    return lints, any(word in hooks for word in ("ruff-format", "black"))


def at_head(names, scratch):
    """The changed files as HEAD has them, under `scratch` with the project's config beside them."""
    for name in list(names) + [c for c in CONFIGS if os.path.isfile(c)]:
        shown = git("show", "HEAD:" + name)
        if shown.returncode == 0:
            path = os.path.join(scratch, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write(shown.stdout)
    return scratch


def violations(tree, names):
    present = [n for n in names if os.path.isfile(os.path.join(tree, n))]
    if not present:
        return collections.Counter(), {}
    found = json.loads(ruff(tree, "check", "--no-fix", "--output-format", "json", *present).stdout or "[]")
    where = {}
    for v in found:
        where.setdefault((os.path.relpath(v["filename"], os.path.realpath(tree)), v["code"]), "%s:%d  %s  %s" % (
            os.path.relpath(v["filename"], os.path.realpath(tree)), v["location"]["row"], v["code"], v["message"]))
    return collections.Counter((os.path.relpath(v["filename"], os.path.realpath(tree)), v["code"]) for v in found), where


def main():
    names = sorted(n for n in set(git("diff", "HEAD", "--name-only").stdout.split()) | set(git("ls-files", "--others", "--exclude-standard").stdout.split())
                   if n.endswith(".py") and os.path.isfile(n) and not n.startswith(("quality/", ".claude/", ".venv/")))
    lints, formats = rules()
    failures = []
    with tempfile.TemporaryDirectory() as scratch:
        base = at_head(names, scratch)
        if lints and names:
            before, _ = violations(base, names)
            after, where = violations(".", names)
            failures += ["new lint violation  " + where[key] for key in sorted((after - before).keys())]
        if formats:
            for name in names:
                was_clean = not os.path.isfile(os.path.join(base, name)) or ruff(base, "format", "--check", name).returncode == 0
                if was_clean and ruff(".", "format", "--check", name).returncode != 0:
                    failures.append("not formatted  %s  (.venv/bin/python -m ruff format %s)" % (name, name))
    if failures:
        print("FAIL: %d place(s) where the change breaks the project's own lint or format rules:" % len(failures))
        for line in failures:
            print("  " + line)
        print("Fix each one; `.venv/bin/python -m ruff check <file>` and `.venv/bin/python -m ruff format <file>` are the project's tools.")
        return 1
    print("ok: %d changed Python file(s) keep the project's lint and format rules" % len(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())

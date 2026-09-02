"""changed — which lines the working tree changed against a base, from git.

`base_ref()` picks what to diff against: an explicit ref, else the pull
request's base when CI says so (`GITHUB_BASE_REF`), else the merge-base with
the default branch, else HEAD — so on the default branch with nothing pushed
"changed" means uncommitted. `changed_lines()` reads `git diff -U0` from that
base to the working tree, plus every line of every untracked file, as
{repo-relative path: set of line numbers}.
"""

import os
import re
import subprocess

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class ChangedError(Exception):
    """git could not be read; the message says why."""


def _git(repo, *args):
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout


def _exists(repo, ref):
    return _git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}")[0] == 0


def base_ref(repo, explicit=None):
    """The ref to diff against — see the module docstring for the order."""
    if explicit:
        return explicit
    pr_base = os.environ.get("GITHUB_BASE_REF")
    if pr_base and _exists(repo, "origin/" + pr_base):
        return "origin/" + pr_base
    for candidate in ("origin/main", "origin/master", "main", "master"):
        if _exists(repo, candidate):
            code, base = _git(repo, "merge-base", candidate, "HEAD")
            if code == 0 and base.strip():
                return base.strip()
    return "HEAD"


def _parse_diff(out):
    changed = {}
    current = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            name = line[4:].strip()
            current = None if name == "/dev/null" else (name[2:] if name.startswith("b/") else name)
            continue
        match = HUNK_RE.match(line)
        if match and current is not None:
            start, count = int(match.group(1)), int(match.group(2) or 1)
            changed.setdefault(current, set()).update(range(start, start + count))
    return changed


def _untracked(repo, changed):
    _, out = _git(repo, "ls-files", "--others", "--exclude-standard")
    for name in out.splitlines():
        path = os.path.join(repo, name)
        if os.path.isfile(path):
            with open(path, errors="replace") as handle:
                changed.setdefault(name, set()).update(range(1, handle.read().count("\n") + 2))
    return changed


def changed_lines(repo, base):
    """{repo-relative path: {line, …}} for every line added or changed between `base`
    and the working tree, untracked files included in full."""
    code, out = _git(repo, "diff", "-U0", "--no-color", "--no-ext-diff", base, "--")
    if code != 0:
        code, out = _git(repo, "diff", "-U0", "--no-color", "--no-ext-diff", "--")
    if code != 0:
        raise ChangedError("git diff failed in %s" % repo)
    return _untracked(repo, _parse_diff(out))

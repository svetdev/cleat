"""churn — how many commits touched each file in a window, from git log."""

import os
import subprocess


class ChurnError(Exception):
    """git could not be read; the message says why."""


def commits_by_file(repo, window_days):
    """{absolute realpath: commit count} for every file a commit in the last
    `window_days` days touched — a file appears at most once per commit in
    `git log --name-only`, so counting lines is counting commits."""
    proc = subprocess.run(
        ["git", "-C", repo, "log", "--since=%d days ago" % window_days, "--name-only", "--pretty=format:"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise ChurnError("git log failed in %s: %s" % (repo, proc.stderr.strip()))
    counts = {}
    for line in proc.stdout.splitlines():
        name = line.strip()
        if name:
            path = os.path.realpath(os.path.join(repo, name))
            counts[path] = counts.get(path, 0) + 1
    return counts

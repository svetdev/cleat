#!/usr/bin/env python3
"""screen — which early stretches of a project's history can be replayed by an agent.

  screen.py <rust|ts> <clones dir> <out.json> [window] [min lines] [max lines]

A stretch is `window` consecutive first-parent changes to production code, starting
where the project had between `min lines` and `max lines` of production code. A
change is usable when the agent could be handed it and scored without upstream's
internals: it adds at most 400 production lines, it changes a test file, every test
file it changes reaches the code through its public interface (Rust: an integration
test under a `tests/` directory; TypeScript: relative imports only of a package's
`src` or `index`), and its message says what it does (a pull request number, or a
body of at least 15 words). Nothing is built or run: this ranks stretches for the
build-and-test validation that follows. Per repository the stretch with the largest
usable share is kept.
"""

import json
import os
import posixpath
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "paired", "rust"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "paired", "typescript"))
import cargo     # noqa: E402  Rust's production and test rules
import tsdraw    # noqa: E402  TypeScript's

MAX_ADDED = 400
PR_RE = re.compile(r"\(#\d+\)|#\d+\b|pull request", re.I)
IMPORT_RE = re.compile(r"""(?:from|import|require\()\s*['"]([^'"]+)['"]""")
MANIFESTS = {"rust": ("Cargo.toml", "Cargo.lock"),
             "ts": ("package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json")}


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, errors="replace").stdout


def history(repo):
    """First-parent changes, oldest first: sha, date, message, and per file (added, deleted)."""
    out = git(repo, "log", "--first-parent", "--diff-merges=first-parent", "--no-renames", "--reverse",
              "--numstat", "--format=\x01%H\x02%cs\x02%B\x03")
    changes = []
    for block in out.split("\x01")[1:]:
        head, _, stat = block.partition("\x03")
        sha, date, message = head.split("\x02", 2)
        files = {}
        for line in stat.strip().splitlines():
            a, d, path = line.split("\t", 2)
            if a != "-":
                files[path] = (int(a), int(d))
        changes.append(dict(sha=sha, date=date, message=message.strip(), files=files))
    return changes


def public_ts(repo, sha, path):
    """Whether a TypeScript test reaches its code only through a package's src or index."""
    text = git(repo, "show", "%s:%s" % (sha, path))
    for spec in IMPORT_RE.findall(text):
        if not spec.startswith("."):
            continue
        target = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))
        parts = re.sub(r"\.[cm]?[jt]sx?$", "", target).split("/")
        if parts[-1] == "index":
            parts = parts[:-1]
        if parts and parts[-1] != "src" and "src" in parts:
            return False
    return True


def classify(lang, repo, change):
    rules = cargo if lang == "rust" else tsdraw
    prod = {p: n for p, n in change["files"].items() if rules.production(p)}
    tests = [p for p in change["files"] if rules.is_test(p)]
    present = set(git(repo, "ls-tree", "-r", "--name-only", change["sha"], "--", *tests).split()) if tests else set()
    tests = [p for p in tests if p in present]
    if lang == "rust":
        public = all("tests" in p.split("/")[:-1] for p in tests)
    else:
        public = all(public_ts(repo, change["sha"], p) for p in tests)
    body = change["message"].split("\n", 1)[1] if "\n" in change["message"] else ""
    return dict(prod=bool(prod), added=sum(a for a, _ in prod.values()), net=sum(a - d for a, d in prod.values()),
                tested=bool(tests), public=bool(tests) and public,
                text=bool(PR_RE.search(change["message"])) or len(body.split()) >= 15,
                manifest=any(os.path.basename(p) in MANIFESTS[lang] for p in change["files"]))


def screen(lang, repo, window, low, high):
    rules = cargo if lang == "rust" else tsdraw
    lines, steps = 0, []
    for change in history(repo):
        before = lines
        lines += sum(a - d for p, (a, d) in change["files"].items() if rules.production(p))
        if any(rules.production(p) for p in change["files"]):
            steps.append((before, change))
    best = None
    rows = {}
    for i, (before, _) in enumerate(steps):
        if not low <= before <= high or i + window > len(steps):
            continue
        stretch = steps[i:i + window]
        for _, c in stretch:
            if c["sha"] not in rows:
                rows[c["sha"]] = classify(lang, repo, c)
        flags = [rows[c["sha"]] for _, c in stretch]
        usable = sum(f["added"] <= MAX_ADDED and f["public"] and f["text"] for f in flags)
        if best is None or usable > best["usable"]:
            end = stretch[-1][0] + flags[-1]["net"]
            best = dict(start=stretch[0][1]["sha"], parent=stretch[0][1]["sha"] + "^", date=stretch[0][1]["date"],
                        end_date=stretch[-1][1]["date"], lines=before, end_lines=end, usable=usable,
                        tested=sum(f["tested"] for f in flags), public=sum(f["public"] for f in flags),
                        text=sum(f["text"] for f in flags), manifest=sum(f["manifest"] for f in flags),
                        added=sum(f["added"] for f in flags))
    return best


def main(lang, clones, out, window="40", low="1500", high="8000"):
    found = {}
    for name in sorted(os.listdir(clones)):
        best = screen(lang, os.path.join(clones, name), int(window), int(low), int(high))
        found[name] = best
        if best:
            print("%-28s %s..%s  %5d -> %5d lines  usable %2d/%s  tested %2d public %2d text %2d manifest %2d" % (
                name, best["date"], best["end_date"], best["lines"], best["end_lines"], best["usable"], window,
                best["tested"], best["public"], best["text"], best["manifest"]), flush=True)
        else:
            print("%-28s no stretch in range" % name, flush=True)
    with open(out, "w") as handle:
        json.dump(dict(window=int(window), low=int(low), high=int(high), repos=found), handle, indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])

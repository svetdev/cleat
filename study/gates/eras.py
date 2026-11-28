#!/usr/bin/env python3
"""eras — do commits written with an AI assistant get refused more than a person's, in the same repository?

  eras.py pick <clone> <out dir> [most]        writes <out dir>/<repo>.ai.txt and <repo>.human.txt
  eras.py report <ai history dir> <human history dir>

`pick` takes the newest first-parent commits (at most `most`, default 40) that change a source
file and whose message credits an assistant: a co-author trailer naming Claude, Copilot,
Cursor, Codex, Devin, Gemini or aider, or a "Generated with" line. Each is matched with the
nearest older commit that changes a source file, credits none, is not by a bot, and is not
already matched, so the two sets come from the same stretch of the same code. A commit
with no such line may still have been written with an assistant: the comparison is of
credited against uncredited, and understates any real gap.

Each list goes through replay/history.py (STUDY_SHAS=<list>, a work dir per set); `report` reads
the two work dirs: per repository and pooled, commits refused in each set, by gate, and the
same within commits of like size. No agent and no tokens.
"""

import glob
import json
import os
import re
import subprocess
import sys

ASSISTED = re.compile(r"co-authored-by:.*\b(claude|copilot|cursor|codex|devin|gemini|aider)\b|generated with \[?(claude|cursor|copilot)|copilot-swe-agent", re.I)
BOT = re.compile(r"\[bot\]|dependabot|renovate|github-actions", re.I)
SOURCE = (".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".swift", ".kt", ".java", ".rb")
GATES = ("complexity", "duplication", "escapes")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, errors="replace").stdout


def log(repo):
    """[(sha, author, whether the message credits an assistant)], newest first."""
    out = []
    for record in git(repo, "log", "--first-parent", "--no-merges", "--format=%H%x1f%an <%ae>%x1f%B%x1e").split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) == 3:
            out.append((parts[0], parts[1], bool(ASSISTED.search(parts[2]) or ASSISTED.search(parts[1]))))
    return out


def changes_source(repo, sha):
    return any(name.endswith(SOURCE) for name in git(repo, "diff", "--name-only", "--no-renames", sha + "^", sha).split("\n"))


def pick(clone, out_dir, most="40"):
    commits, assisted, human, used = log(clone), [], [], set()
    for i, (sha, _, credited) in enumerate(commits):
        if len(assisted) == int(most):
            break
        if not credited or not changes_source(clone, sha):
            continue
        match = next((s for s, author, c in commits[i + 1:] if not c and s not in used and not BOT.search(author) and changes_source(clone, s)), None)
        if match:
            used.add(match)
            assisted.append(sha)
            human.append(match)
    name = os.path.basename(os.path.normpath(clone))
    os.makedirs(out_dir, exist_ok=True)
    for kind, shas in (("ai", assisted), ("human", human)):
        with open(os.path.join(out_dir, "%s.%s.txt" % (name, kind)), "w") as handle:
            handle.write("\n".join(shas) + "\n")
    print("%s: %d credited commits, each matched" % (name, len(assisted)))


def added(row):
    m = re.search(r"(\d+) insertion", row.get("stat", ""))
    return int(m.group(1)) if m else 0


def rate(rows, gate=None):
    hit = sum((gate in r["failing"]) if gate else bool(r["failing"]) for r in rows)
    return "%3d of %3d (%2.0f%%)" % (hit, len(rows), 100 * hit / len(rows)) if rows else "  none"


def report(ai_dir, human_dir):
    sets = {"ai": [], "human": []}
    for path in sorted(glob.glob(os.path.join(ai_dir, "*.json"))):
        other = os.path.join(human_dir, os.path.basename(path))
        if not os.path.isfile(other):
            continue
        ai, human = ([r for r in json.load(open(p)) if "failing" in r] for p in (path, other))
        sets["ai"] += ai
        sets["human"] += human
        print("%-24s credited refused %s | uncredited %s" % (os.path.basename(path)[:-5], rate(ai), rate(human)))
    print("\npooled                   credited refused %s | uncredited %s" % (rate(sets["ai"]), rate(sets["human"])))
    for gate in GATES:
        print("  %-22s credited         %s | uncredited %s" % (gate, rate(sets["ai"], gate), rate(sets["human"], gate)))
    sizes = sorted(added(r) for rows in sets.values() for r in rows)
    cuts = [sizes[len(sizes) // 3], sizes[2 * len(sizes) // 3]] if sizes else [0, 0]
    for label, keep in (("up to %d lines added" % cuts[0], lambda n: n <= cuts[0]),
                        ("%d to %d" % (cuts[0] + 1, cuts[1]), lambda n: cuts[0] < n <= cuts[1]), ("over %d" % cuts[1], lambda n: n > cuts[1])):
        print("  %-22s credited         %s | uncredited %s" % (label, rate([r for r in sets["ai"] if keep(added(r))]), rate([r for r in sets["human"] if keep(added(r))])))
    for kind, rows in sets.items():
        ordered = sorted(added(r) for r in rows)
        print("  %s: median %d lines added" % (kind, ordered[len(ordered) // 2] if ordered else 0))


if __name__ == "__main__":
    {"pick": pick, "report": report}[sys.argv[1]](*sys.argv[2:])

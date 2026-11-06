#!/usr/bin/env python3
"""plan — the changes a replay re-implements, as the 2026-11-03 pre-registration fixes them.

  plan.py <rust|ts> <clones dir> <work dir> <repo> <screen.json> [changes]

The stretch is the `changes` (80) first-parent changes to production code from the start the
screen chose. Each change gets its first parent, its files, the screen's flags, its task
text (its pull request and linked issue, as draw.task_text gives it) and the public names
its production diff adds. A change the screen calls usable is validated on upstream's own
tree: its tests fail before it and pass after it, by the paired runs' rule; one that
validates is scored. The checkpoints are every fifth of the stretch (16, 32, 48, 64 and 80 of
80; 30, 60, 90, 120 and 150 of 150). A checkpoint's probes are the first three scored changes
of upstream's history among the `WINDOW` changes after it, beyond the stretch for the last:
fewer where fewer validate, since a probe further on asks for code no tree has yet (the
2026-11-04 replay's probes after change 80 came 20 to 31 changes on).
Validation outcomes are cached in <work>/validated.jsonl, so a rerun asks only the rest.
Writes <work>/plan.json.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lang as languages   # noqa: E402
import screen              # noqa: E402
import draw                # noqa: E402  (lang put the paired runs on the path)

PROBES = 3
WINDOW = 10   # changes after a checkpoint a probe may come from


def checkpoints(n):
    return tuple(n * k // 5 for k in range(1, 6))


def first_parent(repo, sha):
    return screen.git(repo, "rev-parse", sha + "^1").strip()


def files_of(repo, parent, sha):
    out = []
    for line in screen.git(repo, "diff", "--name-status", "--no-renames", parent, sha).splitlines():
        status, path = line.split("\t", 1)
        out.append([status[0], path, path])
    return out


def public_names(lang, repo, parent, sha, files):
    prods = [p for _, _, p in files if lang.production(p)]
    diff = screen.git(repo, "diff", parent, sha, "--", *prods) if prods else ""
    return sorted(set(lang.public.findall(diff)))


def main(lang_name, clones, work, repo_name, screen_json, changes="80"):
    lang = languages.get(lang_name)
    clones, work, n = os.path.abspath(clones), os.path.abspath(work), int(changes)
    repo = os.path.join(clones, repo_name)
    start = json.load(open(screen_json))["repos"][repo_name]["start"]
    steps = [c for c in screen.history(repo) if any(lang.production(p) for p in c["files"])]
    first = next(i for i, c in enumerate(steps) if c["sha"] == start)
    os.makedirs(work, exist_ok=True)
    cache_path = os.path.join(work, "validated.jsonl")
    cache = {}
    if os.path.isfile(cache_path):
        for line in open(cache_path):
            r = json.loads(line)
            cache[r["sha"]] = r
    out = []

    def entry(i):
        c = steps[i]
        parent = first_parent(repo, c["sha"])
        files = files_of(repo, parent, c["sha"])
        flags = screen.classify(lang_name, repo, c)
        usable = flags["added"] <= screen.MAX_ADDED and flags["public"] and flags["text"]
        e = dict(index=i - first + 1, sha=c["sha"], parent=parent, date=c["date"], message=c["message"],
                 files=files, flags=flags, usable=usable, scored=False)
        if usable:
            if c["sha"] not in cache:
                cand = dict(repo=repo_name, sha=c["sha"], parent=parent, files=files)
                try:
                    task, why = lang.validate(clones, work, cand)
                except subprocess.TimeoutExpired:
                    task, why = None, "timeout"
                cache[c["sha"]] = dict(sha=c["sha"], why=why, task=task)
                with open(cache_path, "a") as handle:
                    handle.write(json.dumps(cache[c["sha"]]) + "\n")
                print(e["index"], c["sha"][:8], why.splitlines()[0][:100] if why else why, flush=True)
            v = cache[c["sha"]]
            e["validation"] = v["why"]
            if v["task"]:
                e.update(v["task"], scored=True)
        return e

    for i in range(first, first + n):
        out.append(entry(i))
    probes = {}
    for point in checkpoints(n):
        chosen, i = [], first + point
        while len(chosen) < PROBES and i < min(len(steps), first + point + WINDOW):
            e = out[i - first] if i - first < len(out) else entry(i)
            if e["scored"]:
                chosen.append(e)
            i += 1
        probes[str(point)] = chosen
    for e in out + [p for ps in probes.values() for p in ps]:
        if "text" not in e:
            text, source = draw.task_text(dict(repo=repo_name, sha=e["sha"], message=e["message"]))
            names = public_names(lang, repo, e["parent"], e["sha"], e["files"])
            if names:
                text += "\n\n--- interface ---\nThe change adds these public names, which its tests may use: %s." % (
                    ", ".join("`%s`" % x for x in names))
            e.update(text=text, source=source, public_names=names)
    plan = dict(lang=lang_name, repo=repo_name, start=start, changes=out, probes=probes,
                scored=sum(e["scored"] for e in out), usable=sum(e["usable"] for e in out))
    with open(os.path.join(work, "plan.json"), "w") as handle:
        json.dump(plan, handle, indent=1)
    print("%s: %d changes, %d usable, %d scored; probes %s" % (
        repo_name, len(out), plan["usable"], plan["scored"],
        {k: [p["index"] for p in v] for k, v in probes.items()}))


if __name__ == "__main__":
    main(*sys.argv[1:])

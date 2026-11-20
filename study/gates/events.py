#!/usr/bin/env python3
"""events — what cleat's event logs say happened: every Stop-hook run and every guarded tool call.

  events.py <dir> [<dir> …]

Every `quality/.events.jsonl` under the directories is read: a study's work dir, or any
project cleat is attached to, where the log is the record of real sessions. Printed: Stop-hook
runs and how many failed, by gate; runs (logs) in which each gate ever failed; guard verdicts,
and what the refused calls were aimed at, split by whether the hook was failing at the time.
A gate that never fails in thousands of hook runs is one the agent never needed.

A line found in more than one log is dropped: a tree copied from a checkout that had a log
of its own carries that log's lines, and counting them once inflated one tally 2.4 times
(DECISIONS.md, 2026-11-19). No agent and no tokens.
"""

import collections
import json
import os
import sys

SKIP = {"node_modules", "target", ".git", "dist", ".venv", "build", "DerivedData", ".gradle", "Pods"}


def logs(roots):
    for root in roots:
        for directory, dirs, files in os.walk(os.path.expanduser(root)):
            dirs[:] = [d for d in dirs if d not in SKIP]
            if os.path.basename(directory) == "quality" and ".events.jsonl" in files:
                yield os.path.join(directory, ".events.jsonl")


def main(*roots):
    paths = sorted(set(logs(roots)))
    lines = {p: [l for l in open(p, errors="replace") if l.strip()] for p in paths}
    seen = collections.Counter(l for own in lines.values() for l in set(own))
    hooks, failed, by_gate, runs_with = 0, 0, collections.Counter(), collections.Counter()
    guard, aimed, refused_runs = collections.Counter(), collections.Counter(), set()
    for path, own in lines.items():
        failing_here = set()
        for line in own:
            if seen[line] > 1:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("mode") == "hook":
                hooks, failed = hooks + 1, failed + (event.get("verdict") == "fail")
                names = [g["name"] for g in event.get("gates", []) if g.get("status") not in ("ok", "skip")]
                by_gate.update(names)
                failing_here.update(names)
            elif event.get("mode") == "guard":
                guard[event.get("verdict")] += 1
                if event.get("verdict") != "allowed":
                    target = event.get("target")
                    aimed[(os.path.basename(str(target)) if target else "a command", bool(event.get("while_red")))] += 1
                    refused_runs.add(path)
        runs_with.update(failing_here)
    print("%d event logs" % len(paths))
    print("Stop-hook runs: %d, failed %d" % (hooks, failed))
    for name, count in by_gate.most_common():
        print("  %-22s failed %5d times, in %d logs" % (name, count, runs_with[name]))
    print("guarded tool calls: %s" % dict(guard))
    print("logs with a refused call: %d" % len(refused_runs))
    for (target, red), count in aimed.most_common(25):
        print("  %4d  %s%s" % (count, target, "  (hook failing)" if red else ""))


if __name__ == "__main__":
    main(*sys.argv[1:])

"""events — what the agent's hooks did, one JSON line per firing, so there are numbers.

`gate.py --hook` and `gate.py --guard` are the two places cleat meets an agent.
Each firing appends one line to `quality/.events.jsonl` beside the project's
config (gitignored; attach adds the entry):

  hook   {"ts", "mode": "hook", "verdict": "pass"|"fail", "again": bool, "head", "changed_files",
          "gates": [{"name", "status": "ok"|"fail"|"err"|"skip", "new", "worsened"}]}
  guard  {"ts", "mode": "guard", "verdict": "blocked"|"allowed", "tool", "target", "head",
          "while_red": bool}   — while_red: the last hook firing before it had failed

The log is on by default and off with `"events": false` in quality.json. An
allowed guard firing records the tool and nothing else: the command an agent
was allowed to run may carry a secret. A refusal records what was refused.

`gate.py --stats [--since 7d]` reads it back: firings, the hook's fail rate, the
gates that fail most, how many failing firings were green on the next one (the
agent fixed it after the hook fed it back), and what the guard refused while a
gate was red. Nothing here changes a verdict; a log that cannot be written is
skipped silently.
"""

import datetime
import json
import os
import re
import subprocess

FILENAME = ".events.jsonl"
NEW_RE = re.compile(r"FAIL: (\d+) new ")
WORSE_RE = re.compile(r"FAIL: (\d+) baselined .* got worse")


def path_for(root):
    return os.path.join(root, "quality", FILENAME)


def enabled(root):
    """Whether the project records events: `"events": false` in quality.json turns it off."""
    try:
        with open(os.path.join(root, "quality.json")) as handle:
            return json.load(handle).get("events", True) is not False
    except (OSError, ValueError):
        return True


def _git(root, *args):
    try:
        proc = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def head(root):
    return _git(root, "rev-parse", "--short", "HEAD")


def changed_files(root):
    out = _git(root, "status", "--porcelain")
    return len(out.splitlines()) if out is not None else None


def gate_result(name, code, output):
    """One gate's line of the hook event: status, and the new / worsened counts the
    engine printed when it failed."""
    status = {0: "ok", 1: "fail"}.get(code, "err")
    new = NEW_RE.search(output)
    worse = WORSE_RE.search(output)
    return {"name": name, "status": status, "new": int(new.group(1)) if new else 0,
            "worsened": int(worse.group(1)) if worse else 0}


def record(root, event):
    """Append `event` with a timestamp; never raise."""
    event = dict(event, ts=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))
    try:
        os.makedirs(os.path.dirname(path_for(root)), exist_ok=True)
        with open(path_for(root), "a") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    except OSError:
        pass


def read(root, since=None):
    """Every event, oldest first, at or after `since` (a datetime) when given."""
    try:
        with open(path_for(root)) as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if since is None or event.get("ts", "") >= since.isoformat(timespec="seconds"):
            out.append(event)
    return out


def last_hook_failed(root):
    """Whether the most recent hook firing failed — what a guard event means by 'while red'."""
    hooks = [e for e in read(root) if e.get("mode") == "hook"]
    return bool(hooks) and hooks[-1].get("verdict") == "fail"


def parse_since(text):
    """A datetime `text` ago: '7d', '24h', '30m'."""
    match = re.fullmatch(r"(\d+)([dhm])", text or "")
    if not match:
        raise ValueError("--since takes a number and d, h or m — 7d, 24h, 30m")
    amount, unit = int(match.group(1)), match.group(2)
    delta = {"d": datetime.timedelta(days=amount), "h": datetime.timedelta(hours=amount), "m": datetime.timedelta(minutes=amount)}[unit]
    return datetime.datetime.now(datetime.timezone.utc) - delta


def _resolved_next(hooks):
    """How many failing hook firings were followed by a passing one."""
    return sum(1 for a, b in zip(hooks, hooks[1:]) if a.get("verdict") == "fail" and b.get("verdict") == "pass")


def _top_failing(hooks):
    counts = {}
    for event in hooks:
        for gate in event.get("gates", []):
            if gate.get("status") == "fail":
                counts[gate["name"]] = counts.get(gate["name"], 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _hook_rows(hooks):
    fails = [e for e in hooks if e.get("verdict") == "fail"]
    rate = "%.0f%%" % (100.0 * len(fails) / len(hooks)) if hooks else "-"
    return [("hook firings", len(hooks)), ("  failed", len(fails)), ("  fail rate", rate),
            ("  fixed by the next firing", _resolved_next(hooks)),
            ("  blocked twice in a row (let go)", sum(1 for e in fails if e.get("again")))]


def _guard_rows(guards):
    blocked = [e for e in guards if e.get("verdict") == "blocked"]
    return [("guard firings", len(guards)), ("  refused", len(blocked)),
            ("  refused while a gate was red", sum(1 for e in blocked if e.get("while_red")))]


def stats(events):
    """The numbers, as (label, value) rows."""
    hooks = [e for e in events if e.get("mode") == "hook"]
    guards = [e for e in events if e.get("mode") == "guard"]
    rows = _hook_rows(hooks) + _guard_rows(guards)
    rows += [("  gate failed: %s" % name, count) for name, count in _top_failing(hooks)[:5]]
    return rows

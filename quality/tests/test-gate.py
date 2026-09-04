#!/usr/bin/env python3
"""test-gate — assert the one entrypoint, quality/bin/gate.py.

Over a throwaway checkout whose quality.json configures a document ceiling and
an escapes gate: --list names the gates in ladder order; a green tree passes
with a status row per gate; a failing gate is reported with its output and the
run exits 1; --gate runs one; an unknown --gate is refused naming the
configured ones; --hook sends the failures to stderr and exits 2 (what an
agent's Stop hook hands back); --guard refuses a PreToolUse event that writes a
baseline, edits quality.json or the gates, and allows running a gate, reading
the config, or a malformed event. Writes nothing outside a temporary directory.

  python3 quality/tests/test-gate.py
"""
import json, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "gate.py")
suite = Suite("test-gate"); check = suite.check
ESCAPE = "x = 1  # noqa\n"   # the one fixture escape every case below plants and expects



def run(*args, stdin=None, cwd=None):
    proc = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, input=stdin, cwd=cwd,
                          stdin=subprocess.DEVNULL if stdin is None else None)
    return proc.returncode, proc.stdout, proc.stderr


def guard(event):
    # from the fixture tree, so a firing is logged there and never in this repository's own log
    return run("--guard", stdin=json.dumps(event) if not isinstance(event, str) else event, cwd=tmp)


tmp = tempfile.mkdtemp(prefix="gate-")
try:
    write(os.path.join(tmp, "README.md"), "one two three\n")
    write(os.path.join(tmp, "src", "a.py"), "x = 1\n")
    config = os.path.join(tmp, "quality.json")
    write(config, json.dumps({
        "escapes": {"roots": ["src"], "languages": ["python"], "baseline": "escapes-baseline.json"},
        "doc_size": [{"file": "README.md", "ceiling": 10}]}))
    code, out, err = run("--config", config, "--list")
    check("--list names the configured gates in ladder order", code == 0 and out.split() == ["doc-size", "escapes"], out + err)
    code, out, err = run("--config", config)
    check("a green tree passes with a row per gate", code == 0 and "ok    doc-size" in out and "ok    escapes" in out and "2 gate(s), all passed." in out, out + err)

    write(os.path.join(tmp, "src", "a.py"), ESCAPE)
    code, out, err = run("--config", config)
    check("a failing gate fails the run", code == 1 and "FAIL  escapes" in out and "1 failed." in out, out + err)
    check("with the gate's own output, indented under its row", "src/a.py:1  noqa" in out, out)
    check("and the green gate still shows as ok", "ok    doc-size" in out, out)
    code, out, err = run("--config", config, "--gate", "doc-size")
    check("--gate runs one gate", code == 0 and "escapes" not in out, out + err)
    code, out, err = run("--config", config, "--gate", "nope")
    check("an unknown --gate is refused naming the configured ones", code == 2 and "nope" in err and "doc-size, escapes" in err, err)
    code, out, err = run("--config", config, "--hook")
    check("--hook exits 2 with the failures on stderr", code == 2 and "[escapes]" in err and "src/a.py:1  noqa" in err and "fix what each names" in err, err)
    code, out, err = run("--config", config, "--hook", "--gate", "doc-size")
    check("--hook exits 0 when everything passes", code == 0 and err == "", err)
    code, out, err = run("--config", config, "--hook", stdin=json.dumps({"stop_hook_active": True, "hook_event_name": "Stop"}))
    check("--hook does not block a second consecutive stop: the failures are reported and the agent may stop", code == 0 and "[escapes]" in err and "not blocking a second time" in err, err)
    code, out, err = run("--config", config, "--hook", stdin=json.dumps({"stop_hook_active": False}))
    check("with stop_hook_active false it blocks as before", code == 2, err)

    # ---- the event log: every hook and guard firing is one JSON line; --stats reads them back
    events_path = os.path.join(tmp, "quality", ".events.jsonl")
    check("the failing hook runs above were recorded", os.path.isfile(events_path), events_path)
    lines = [json.loads(l) for l in open(events_path).read().splitlines()]
    hooks = [e for e in lines if e["mode"] == "hook"]
    check("a hook event carries the verdict and each gate's result with its new-violation count",
          hooks and hooks[0]["verdict"] == "fail" and any(g["name"] == "escapes" and g["status"] == "fail" and g["new"] == 1 for g in hooks[0]["gates"]), str(hooks[:1]))
    check("the second-stop firing is marked", any(e.get("again") for e in hooks), str(hooks))
    proc = subprocess.run([sys.executable, SCRIPT, "--guard"], capture_output=True, text=True, cwd=tmp,
                          input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": os.path.join(tmp, "quality.json")}}))
    guards = [json.loads(l) for l in open(events_path).read().splitlines() if '"guard"' in l]
    check("a guard refusal is recorded with the target, and that a gate was red at the time",
          proc.returncode == 2 and guards and guards[-1]["verdict"] == "blocked" and guards[-1]["target"].endswith("quality.json") and guards[-1]["while_red"] is True, str(guards[-1:]))
    proc = subprocess.run([sys.executable, SCRIPT, "--guard"], capture_output=True, text=True, cwd=tmp,
                          input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "curl -H 'Authorization: Bearer s3cret' https://x"}}))
    allowed = json.loads(open(events_path).read().splitlines()[-1])
    check("an allowed guard firing records the tool and never the command", proc.returncode == 0 and allowed["verdict"] == "allowed" and allowed["target"] is None and "s3cret" not in open(events_path).read(), str(allowed))
    write(os.path.join(tmp, "src", "a.py"), "x = 1\n")
    write(config, json.dumps({"escapes": {"roots": ["src"], "languages": ["python"], "baseline": "escapes-baseline.json"},
                              "doc_size": [{"file": "README.md", "ceiling": 10}]}))
    code, out, err = run("--config", config, "--hook")
    check("a passing hook firing is recorded too", code == 0 and json.loads(open(events_path).read().splitlines()[-1])["verdict"] == "pass", err)
    code, out, err = run("--config", config, "--stats")
    check("--stats reports firings, the fail rate, the fixes and the refusals", code == 0 and "hook firings" in out and "fixed by the next firing" in out and "refused while a gate was red" in out and "gate failed: escapes" in out, out + err)
    fixed = [l for l in out.splitlines() if "fixed by the next firing" in l][0]
    check("the fix after the hook fed it back is counted", int(fixed.split()[-1]) >= 1, fixed)
    code, out, err = run("--config", config, "--stats", "--since", "1m")
    check("--since narrows the window", code == 0 and "since 1m" in out, out + err)
    before_lines = len(open(events_path).read().splitlines())
    write(config, json.dumps({"events": False, "escapes": {"roots": ["src"], "languages": ["python"], "baseline": "escapes-baseline.json"}}))
    run("--config", config, "--hook")
    subprocess.run([sys.executable, SCRIPT, "--guard"], capture_output=True, text=True, cwd=tmp, input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "quality.json"}}))
    check('"events": false records nothing, for the hook or the guard', len(open(events_path).read().splitlines()) == before_lines)
    write(config, json.dumps({"escapes": {"roots": ["src"], "languages": ["python"], "baseline": "escapes-baseline.json"},
                              "doc_size": [{"file": "README.md", "ceiling": 10}]}))
    code, out, err = run("--config", config, "--stats", "--since", "soon")
    check("a bad --since is refused", code == 2 and "7d, 24h, 30m" in err, err)
    write(os.path.join(tmp, "src", "a.py"), ESCAPE)

    write(config, json.dumps({"project": "x"}))
    code, out, err = run("--config", config)
    check("a config with no gate is refused", code == 2 and "configures no gate" in err, err)

    # ---- --changed: the scoped gates judge the files changed against the base only
    repo = os.path.join(tmp, "repo")
    write(os.path.join(repo, "src", "old.py"), ESCAPE)     # committed, never baselined: CI's problem, not the hook's
    write(os.path.join(repo, "src", "same.py"), "y = 2\n")
    write(os.path.join(repo, "quality.json"), json.dumps({"escapes": {"roots": ["src"], "languages": ["python"], "baseline": "escapes-baseline.json"},
                                                          "duplication": {"roots": ["src"], "languages": ["python"], "baseline": "dup.json"}}))
    subprocess.run(["git", "-C", repo, "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-q", "-m", "files"], check=True)
    write(os.path.join(repo, "src", "new.py"), "z = 3  # type: " + "ignore\n")   # split so this repository's own escapes gate does not read a fixture as a site
    code, out, err = run("--config", os.path.join(repo, "quality.json"), "--changed")
    check("--changed says how many files it judges", "changed: 1 file(s)" in out, out + err)
    check("the changed file's new escape fails", code == 1 and "src/new.py:1  type ignore" in out, out + err)
    check("an unchanged file's unbaselined escape is not judged in changed mode", "src/old.py" not in out, out)
    check("duplication judges changed lines only and needs no baseline", "ok    duplication" in out, out)
    code, out, err = run("--config", os.path.join(repo, "quality.json"))
    check("the full run judges the unchanged file too", code == 1 and "src/old.py:1  noqa" in out, out + err)

    # ---- a section's old name beside its new one is one gate, not two
    write(config, json.dumps({"complexity": {"tool": "lizard", "sources": ["src"], "languages": ["python"], "ceilings": {"cc": 8, "lines": 60}, "baseline": "c.json"},
                              "complexity_lizard": {"sources": ["src"], "languages": ["python"], "ceilings": {"cc": 8, "lines": 60}, "baseline": "c.json"}}))
    code, out, err = run("--config", config, "--list")
    check("complexity and complexity_lizard together list one gate", code == 0 and out.split() == ["complexity"], out + err)

    # ---- --skip-missing-tools: a gate whose tool this machine lacks is reported, not failed
    write(config, json.dumps({"doc_size": [{"file": "README.md", "ceiling": 10}],
                              "complexity": {"tool": "swiftlint", "sources": ["src"], "ceilings": {"cc": 8, "lines": 60}, "baseline": "c.json"},
                              "layering": {"references": "ast-grep", "language": "kotlin", "skip_dirs": [], "allowed": {}, "always_allowed": [], "roots": [{"root": "src", "exempt": []}]}}))
    bare = {"PATH": "/nonexistent", "HOME": os.environ.get("HOME", "/")}  # the scripts run by absolute interpreter path
    proc = subprocess.run([sys.executable, SCRIPT, "--config", config, "--strict", "--skip-missing-tools"], capture_output=True, text=True, env=bare)
    check("--skip-missing-tools skips the gates whose tools are absent, naming the tool, and passes the rest",
          proc.returncode == 0 and "skip  complexity (swiftlint not installed)" in proc.stdout and "skip  layering (ast-grep not installed)" in proc.stdout and "ok    doc-size" in proc.stdout, proc.stdout + proc.stderr)
    proc = subprocess.run([sys.executable, SCRIPT, "--config", config, "--strict"], capture_output=True, text=True, env=bare)
    check("without it the same gates are errors", proc.returncode == 1 and "ERR   complexity" in proc.stdout, proc.stdout + proc.stderr)

    # ---- the `gates` list: the same check over different facts, each a named gate
    write(os.path.join(tmp, "web", "b.ts"), "const a: any = 1;\n")
    write(config, json.dumps({"project": "x", "gates": [
        {"name": "escapes-src", "check": "escapes", "with": {"roots": ["src"], "languages": ["python"], "baseline": "src-escapes.json"}},
        {"name": "escapes-web", "check": "escapes", "with": {"roots": ["web"], "languages": ["typescript"], "baseline": "web-escapes.json"}}]}))
    code, out, err = run("--config", config, "--list")
    check("a gates list yields one named gate per entry", code == 0 and out.split() == ["escapes-src", "escapes-web"], out + err)
    code, out, err = run("--config", config)
    check("each runs its check over its own facts", code == 1 and "FAIL  escapes-src" in out and "src/a.py:1  noqa" in out and "FAIL  escapes-web" in out and "web/b.ts:1  any" in out, out + err)
    check("and the per-gate config file is cleaned up", not [f for f in os.listdir(tmp) if f.startswith(".cleat-gate-")], str(os.listdir(tmp)))
    subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "bin", "check-escapes.py"), "--config", config, "--write-baseline"], capture_output=True)
    write(config, json.dumps({"project": "x", "gates": [{"name": "bad", "check": "nonsense", "with": {}}]}))
    code, out, err = run("--config", config)
    check("an unknown check in the list is refused naming the known ones", code == 2 and "nonsense" in err and "escapes" in err, err)

    refused = [
        {"tool_name": "Bash", "tool_input": {"command": "python3 quality/bin/check-escapes.py --write-baseline"}},
        {"tool_name": "Bash", "tool_input": {"command": "cd /x && sed -i '' 's/8/80/' quality.json"}},
        {"tool_name": "Bash", "tool_input": {"command": "echo '[]' > quality/escapes-baseline.json"}},
        {"tool_name": "Bash", "tool_input": {"command": "cp /tmp/loose.json quality/complexity-baseline.json"}},
        {"tool_name": "Bash", "tool_input": {"command": "rm quality/bin/check-escapes.py"}},
        {"tool_name": "Edit", "tool_input": {"file_path": "/repo/quality.json"}},
        {"tool_name": "Write", "tool_input": {"file_path": "/repo/quality/escapes-baseline.json"}},
        {"tool_name": "Edit", "tool_input": {"file_path": "/repo/quality/bin/ratchet.py"}},
        {"tool_name": "Edit", "tool_input": {"file_path": "/repo/.claude/settings.json"}},
    ]
    for event in refused:
        code, out, err = guard(event)
        check("--guard refuses: %s" % (event["tool_input"].get("command") or event["tool_input"].get("file_path")),
              code == 2 and "refused" in err, err)
    allowed = [
        {"tool_name": "Bash", "tool_input": {"command": "python3 quality/bin/gate.py --strict"}},
        {"tool_name": "Bash", "tool_input": {"command": "python3 quality/bin/check-escapes.py"}},
        {"tool_name": "Bash", "tool_input": {"command": "cat quality.json"}},
        {"tool_name": "Bash", "tool_input": {"command": "git diff quality/"}},
        {"tool_name": "Edit", "tool_input": {"file_path": "/repo/src/quality_of_life.py"}},
        {"tool_name": "Write", "tool_input": {"file_path": "/repo/docs/quality.md"}},
    ]
    for event in allowed:
        code, out, err = guard(event)
        check("--guard allows: %s" % (event["tool_input"].get("command") or event["tool_input"].get("file_path")), code == 0, err)
    code, out, err = guard("not json")
    check("--guard lets a malformed event through rather than blocking on its own bug", code == 0, err)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

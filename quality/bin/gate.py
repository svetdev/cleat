#!/usr/bin/env python3
"""gate — one entrypoint over every gate quality.json configures.

Reads the sections quality.json carries and runs the check each one belongs
to, in the order the adoption ladder recommends — the cheapest first, so a
failing document ceiling is reported before a complexity run starts. Prints a
status row per gate and the failing gates' output, and exits non-zero when
any failed. The preflight is the sections; nothing here has to be listed
twice.

  quality/bin/gate.py                  # every preflight gate, quietly
  quality/bin/gate.py --strict         # CI: a baseline looser than the code fails too
  quality/bin/gate.py --skip-missing-tools   # a gate whose tool this machine lacks is reported as skipped
  quality/bin/gate.py --postflight     # also the gates that read a coverage run (crap)
  quality/bin/gate.py --gate escapes   # one gate, by name
  quality/bin/gate.py --list           # the gates this quality.json configures
  quality/bin/gate.py --hook           # for an agent's Stop hook: failures go to stderr, exit 2
  quality/bin/gate.py --guard          # for an agent's PreToolUse hook: refuse commands that rewrite policy

Gates come from two places in quality.json. Each configured section is a gate
(the sugar every existing config uses), and a `gates` list adds named ones —
the same check over different facts:

  "gates": [
    {"name": "complexity-backend", "check": "complexity",
     "with": {"sources": ["backend"], "languages": ["rust"], "ceilings": {"cc": 8, "lines": 60},
              "baseline": "quality/cc-backend.json"}},
    {"name": "complexity-web", "check": "complexity",
     "with": {"sources": ["web/src"], "languages": ["typescript"], "ceilings": {"cc": 10, "lines": 80},
              "baseline": "quality/cc-web.json"}}
  ]

`--hook` is how the ratchet sits inside an agent's loop. Claude Code's Stop
hook treats exit 2 as "not done" and hands stderr back to the model, so a
failing gate becomes the next thing the agent works on, with the fix in
front of it. `--guard` reads a PreToolUse event from stdin and exits 2 —
refusing the call — when the command would write a baseline, edit
quality.json, or edit the gates: those are policy changes for a person.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import quality_config
import runlock

# section → (gate name, script, accepts --strict, postflight), ladder order
GATES = [
    ("doc_size", "doc-size", "check-doc-size.py", False, False),
    ("doc_citations", "doc-citations", "check-doc-citations.py", False, False),
    ("hygiene", "test-hygiene", "check-test-hygiene.py", False, False),
    ("escapes", "escapes", "check-escapes.py", True, False),
    ("guard_suites", "guard-suites", "check-guard-suites.py", False, False),
    ("duplication", "duplication", "check-duplication.py", True, False),
    ("sarif", "sarif", "check-sarif.py", True, False),
    ("public_api", "public-api", "check-public-api.py", True, False),
    ("manifests", "manifests", "check-manifests.py", False, False),
    ("inventory", "inventory", "check-inventory.py", True, False),
    ("complexity", "complexity", "check-complexity.py", True, False),
    ("complexity_lizard", "complexity", "check-complexity.py", True, False),   # the section's old name
    ("layering", "layering", "check-layering.py", False, False),
    ("reachability", "reachability", "check-reachability.py", False, False),
    ("changed_coverage", "changed-coverage", "check-changed-coverage.py", False, True),
    ("crap", "crap", "check-crap.py", True, True),
]
BY_SECTION = {section: (name, script, strict, postflight) for section, name, script, strict, postflight in GATES}
# The sections that may be a list of named entries, each its own gate selected with --gate.
LISTABLE = {"crap", "sarif", "public_api", "inventory"}
# Sections whose check was retired, and what replaced them.
RETIRED = {"features_map": "split into \"doc_citations\" (the map's citations) and \"reachability\" (the services nothing constructs)"}

# What --guard refuses: a command that rewrites accepted debt or edits policy —
# writing a baseline, or a shell edit/copy/redirect aimed at quality.json, the
# baselines, the gates, CODEOWNERS or the agent settings. Running a gate is fine.
POLICY_PATHS = r"(?:quality\.json|quality/|\.github/CODEOWNERS|\.claude/settings)"
GUARDED_COMMAND_RE = re.compile(
    r"--write-baseline"
    r"|\b(?:sed\s+-[a-zA-Z]*i|tee|cp|mv|rm|truncate|install)\b[^\n|;&]*" + POLICY_PATHS +
    r"|>{1,2}\s*['\"]?(?:\S*/)?" + POLICY_PATHS
)
GUARDED_PATH_RE = re.compile(r"(?:^|/)" + POLICY_PATHS)


class Gate:
    def __init__(self, name, script, strict, postflight, extra=(), section=None, spec=None):
        self.name = name
        self.script = os.path.join(HERE, script)
        self.strict = strict
        self.postflight = postflight
        self.extra = list(extra)
        self.section = section   # for a `gates` entry: the section key its check reads …
        self.spec = spec         # … and the object to put there

    def command(self, config_path, strict):
        cmd = [self.script] if self.script.endswith(".sh") else [sys.executable, self.script]
        cmd += ["--config", config_path, "--quiet"] + self.extra
        if strict and self.strict:
            cmd.append("--strict")
        return cmd


def _gates_of(section, name, script, strict, postflight, raw):
    """The Gate(s) one section configures: one per entry when it is a list of named ones."""
    if section in LISTABLE and isinstance(raw, list):
        return [Gate("%s:%s" % (name, e.get("name")), script, strict, postflight, ["--gate", str(e.get("name"))]) for e in raw]
    return [Gate(name, script, strict, postflight)]


def from_sections(config):
    """One Gate per configured section, ladder order; a listable section with a list of
    named entries becomes one Gate per entry; a section's old name beside its new one is
    the same gate once."""
    for section, replacement in RETIRED.items():
        if section in config.data:
            raise KeyError("%s: \"%s\" was retired — %s" % (config.file, section, replacement))
    gates, names = [], set()
    for section, name, script, strict, postflight in GATES:
        raw = config.data.get(section)
        if raw is None or name in names:
            continue
        names.add(name)
        gates += _gates_of(section, name, script, strict, postflight, raw)
    return gates


def from_list(config):
    """One Gate per entry of the `gates` list — `{"name", "check", "with"}`, where `check`
    is a section name and `with` what that check would read from it. The same check
    can run several times with different facts: a complexity gate per package, say."""
    gates = []
    for entry in config.data.get("gates", []):
        check = entry.get("check")
        if check not in BY_SECTION:
            raise KeyError("%s: gate %r: unknown check %r — one of: %s"
                           % (config.file, entry.get("name"), check, ", ".join(BY_SECTION)))
        _, script, strict, postflight = BY_SECTION[check]
        name = entry.get("name") or check
        gates.append(Gate(name, script, strict, entry.get("postflight", postflight), section=check, spec=entry.get("with", {})))
    return gates


def configured(config):
    """Every gate this quality.json configures: the sections as sugar, then the list."""
    return from_sections(config) + from_list(config)


def run(gate, config_path, strict):
    """Run one gate. A `gates` entry gets a config of its own beside quality.json — the
    check reads its usual section, filled from the entry's `with`, paths relative to the
    same directory — and that file is removed after."""
    root = os.path.dirname(config_path)
    path = config_path
    if gate.section is not None:
        with open(config_path) as handle:
            base = json.load(handle)
        path = os.path.join(root, ".cleat-gate-%s.json" % re.sub(r"[^\w.-]", "_", gate.name))
        with open(path, "w") as handle:
            json.dump({"project": base.get("project", ""), gate.section: gate.spec}, handle)
    try:
        proc = subprocess.run(gate.command(path, strict), capture_output=True, text=True, cwd=root)
    finally:
        if path != config_path and os.path.exists(path):
            os.remove(path)
    return proc.returncode, (proc.stdout + proc.stderr).rstrip("\n")


def guard(event_text):
    """Exit 2 with a reason when the PreToolUse event on stdin is a command that would
    rewrite policy; 0 otherwise. A malformed event is allowed through — the guard
    refuses what it can read, it does not block the agent on its own bugs."""
    try:
        event = json.loads(event_text or "{}")
    except ValueError:
        return 0
    tool_input = event.get("tool_input") or {}
    command = tool_input.get("command") or ""
    target = tool_input.get("file_path") or ""
    if not (GUARDED_COMMAND_RE.search(command) or GUARDED_PATH_RE.search(target)):
        return 0
    print("cleat: refused — this would change quality policy (a baseline, quality.json, the gates, or the hooks). "
          "Fix the code the gate names instead; policy changes are made by a person in a reviewed commit.",
          file=sys.stderr)
    return 2


def select(args, gates):
    """The gates to run: the named ones, else every preflight gate (every gate with
    --postflight). Raises KeyError naming a gate that is not configured."""
    wanted = set(args.gate or ())
    names = [g.name for g in gates]
    unknown = sorted(wanted - set(names))
    if unknown:
        raise KeyError("no gate named %s — configured: %s" % (", ".join(unknown), ", ".join(names)))
    if wanted:
        return only(gates, lambda g: g.name in wanted)
    if args.postflight:
        return gates
    return only(gates, lambda g: not g.postflight)


def only(gates, keep):
    return [g for g in gates if keep(g)]


def _status(code):
    return "ok  " if code == 0 else ("FAIL" if code == 1 else "ERR ")


def run_all(gates, config_path, strict, skip_missing=False, config=None):
    """Run each gate, print its status row and output, and return the failures. With
    `skip_missing`, a gate whose tool is not installed is reported as skipped, not run."""
    failures = []
    for g in gates:
        absent = missing_tools(g, config) if skip_missing else []
        if absent:
            print("  skip  %s (%s not installed)" % (g.name, ", ".join(absent)))
            continue
        code, out = run(g, config_path, strict)
        print("  %s  %s" % (_status(code), g.name))
        for line in out.splitlines():
            print("        " + line)
        if code != 0:
            failures.append((g, out))
    print("gate: %d gate(s), %s" % (len(gates), "all passed." if not failures else "%d failed." % len(failures)))
    return failures


def _complexity_tool(spec):
    if spec.get("tool"):
        return spec["tool"]
    return "lizard" if spec.get("languages") else "swiftlint"


def tools_for(section, spec):
    """The executables a gate over `spec` (the section's object) needs on the PATH."""
    if not isinstance(spec, dict):
        return []
    if section in ("complexity", "complexity_lizard"):
        return [_complexity_tool(spec)]
    if section == "crap":
        return [_complexity_tool(spec.get("complexity") or {"tool": "swiftlint"})] + (["xcrun"] if "xccov" in spec else [])
    if section in ("layering", "reachability") and spec.get("references") == "ast-grep":
        return ["ast-grep"]
    return []


def _spec_of(gate, config):
    """(section, its object) for a gate from a section — the named entry of a list."""
    section = next((s for s, n, *_ in GATES if n == gate.name.split(":")[0]), None)
    raw = config.data.get(section)
    if not isinstance(raw, list):
        return section, raw
    wanted = gate.name.split(":", 1)[-1]
    return section, next((e for e in raw if str(e.get("name")) == wanted), None)


def missing_tools(gate, config):
    """The tools `gate` needs that are not installed."""
    section, spec = (gate.section, gate.spec) if gate.spec is not None else _spec_of(gate, config)
    return [tool for tool in tools_for(section, spec) if not shutil.which(tool)]


def fail(message):
    print("FAIL: %s" % message, file=sys.stderr)
    return 2


def finish(failures, hook):
    """The exit code — and, in hook mode, the failures again on stderr, which is what
    the agent's harness hands back to it."""
    if failures and hook:
        print("cleat: %d quality gate(s) failed — fix what each names, then stop again:" % len(failures), file=sys.stderr)
        for g, out in failures:
            print("[%s]\n%s" % (g.name, out), file=sys.stderr)
        return 2
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description="run every gate quality.json configures")
    parser.add_argument("--strict", action="store_true", help="a baseline looser than the code fails too (CI)")
    parser.add_argument("--postflight", action="store_true", help="include the gates that read a coverage run")
    parser.add_argument("--skip-missing-tools", action="store_true",
                        help="report, rather than fail, a gate whose tool (swiftlint, lizard, ast-grep, xcrun) is not installed — for a CI runner that cannot have every tool")
    parser.add_argument("--gate", action="append", help="run only this gate (repeatable)")
    parser.add_argument("--list", action="store_true", help="print the configured gates and exit")
    parser.add_argument("--hook", action="store_true", help="agent Stop hook mode: failures to stderr, exit 2")
    parser.add_argument("--guard", action="store_true", help="agent PreToolUse hook mode: refuse policy-changing commands")
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    if args.guard:
        return guard(sys.stdin.read())
    config = quality_config.load(args.config)
    try:
        gates = select(args, configured(config))
    except KeyError as problem:
        return fail(problem.args[0])
    if args.list:
        print("\n".join(g.name for g in gates))
        return 0
    if not gates:
        return fail("%s configures no gate — see quality/README.md" % config.file)
    with runlock.held(os.path.dirname(HERE), "gate.py"):
        failures = run_all(gates, config.file, args.strict, args.skip_missing_tools, config)
    return finish(failures, args.hook)


if __name__ == "__main__":
    sys.exit(main())

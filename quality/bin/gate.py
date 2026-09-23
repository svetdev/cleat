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
  quality/bin/gate.py --hook --changed # for an agent's Stop hook: the changed files, failures to stderr, exit 2
  quality/bin/gate.py --guard          # for an agent's PreToolUse hook: refuse commands that rewrite policy
  quality/bin/gate.py --stats [--since 7d]   # what the two hooks did: firings, fail rate, fixes, refusals

Gates come from three places in quality.json. Each configured section is a gate
(the sugar every existing config uses); a `gates` list adds named ones — the
same check over different facts; and a `commands` list runs the project's own
checks beside them, so this one command is the whole preflight and no wrapper
script has to list gates:

  "commands": [
    {"name": "migrations", "run": "scripts/check-migrations.py"},
    {"name": "api-compat", "run": "scripts/check-api-compat.sh", "needs": ["oasdiff"]},
    {"name": "web-lint", "run": "cd apps/web && npx eslint src", "needs": ["npx"], "postflight": true}
  ]


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
front of it. It blocks once per distinct failure set, not once per stop:
the report that blocked is fingerprinted under `quality/.running/`, and a
later stop carrying the identical report gets one line and exit 0 rather
than the whole report again, so a failure the agent cannot fix — a policy
question for a person — stops costing a report every turn. The event's
`stop_hook_active` (the agent is already continuing because of this hook)
reads the same way. Any change in any gate's output — a file fixed, a file
broken, a count moved — is a new report and blocks again; CI refuses
whatever stays red. The hook runs every baselined gate with `--tighten`: an
entry the change fixed is dropped and an improved one lowered as the agent
goes — never added, never raised — so the next `--strict` run does not fail on
a fix. `--guard` reads a PreToolUse event from stdin and exits 2 —
refusing the call — when the command would write a baseline, edit
quality.json, or edit the gates: those are policy changes for a person. It
judges the files a command writes, resolved from the event's working
directory: a sed script, a comment or a note that names quality.json is text,
and a file outside every project is not policy.
"""

import argparse
import hashlib
import json
import os
import re
import select as selectors  # `select` is a function below
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import events
import quality_config
import runlock
from extractors import changed, shell

# section → (gate name, script, accepts --strict, postflight), ladder order
GATES = [
    ("doc_size", "doc-size", "check-doc-size.py", False, False),
    ("doc_citations", "doc-citations", "check-doc-citations.py", False, False),
    ("hygiene", "test-hygiene", "check-test-hygiene.py", False, False),
    ("escapes", "escapes", "check-escapes.py", True, False),
    ("conventions", "conventions", "check-conventions.py", True, False),
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
    ("dead_symbols", "dead-symbols", "check-dead-symbols.py", False, False),
    ("changed_coverage", "changed-coverage", "check-changed-coverage.py", False, True),
    ("crap", "crap", "check-crap.py", True, True),
]
BY_SECTION = {section: (name, script, strict, postflight) for section, name, script, strict, postflight in GATES}
# Gates that take --only FILES (the changed files, under --changed), and the one that takes --changed-only.
SCOPED = {"complexity", "escapes", "conventions", "crap"}
CHANGED_ONLY = {"duplication"}
# Gates that take --tighten, which the Stop hook passes: a baseline entry the agent's change
# fixed is dropped and an improved one lowered as it goes, so --strict never fails on a fix.
TIGHTENS = {"escapes", "conventions", "duplication", "sarif", "complexity", "crap"}
# The sections that may be a list of named entries, each its own gate selected with --gate.
LISTABLE = {"crap", "sarif", "public_api", "inventory"}
# Sections whose check was retired, and what replaced them.
RETIRED = {"features_map": "split into \"doc_citations\" (the map's citations) and \"reachability\" (the services nothing constructs)"}

# What --guard refuses: a command that rewrites accepted debt or edits policy —
# running a program with --write-baseline, or a shell edit/copy/redirect, an Edit or a
# Write aimed at quality.json, the baselines, the gates, CODEOWNERS or the agent
# settings. Running a gate is fine, and so is a command that only mentions the flag or
# a policy file: a commit message, a heredoc into a document, a grep, a sed over a note
# elsewhere (extractors/shell.py tells a call from a mention, and names the files a
# command writes). Policy lives in a project — a directory holding quality.json — at
# these paths; the agent settings, which hold the hooks, are policy wherever they are.
POLICY_PATHS = r"(?:quality\.json|quality/|\.github/CODEOWNERS|\.claude/settings)"
BASELINE_FLAG = "--write-baseline"
BASELINE_FLAG_SHORTEST = "--wr"   # argparse expands an unambiguous prefix; --w is --web-sources in check-crap
PROJECT_POLICY_RE = re.compile(r"(?:quality\.json|quality(?:/.*)?|\.github/CODEOWNERS|\.claude/settings.*)")
HOOK_SETTINGS_RE = re.compile(r"(?:^|/)\.claude/settings[^/]*$")
GUARDED_PATH_RE = re.compile(r"(?:^|/)" + POLICY_PATHS)   # by name alone: a relative path after a `cd`


class Gate:
    def __init__(self, name, script, strict, postflight, extra=(), section=None, spec=None, shell=None, needs=(), advisory=False):
        self.name = name
        self.advisory = advisory  # a look ahead (CRAP's --estimate): its output is shown, it never fails the run
        self.script = os.path.join(HERE, script) if script else None
        self.strict = strict
        self.postflight = postflight
        self.extra = list(extra)
        self.section = section   # for a `gates` entry: the section key its check reads …
        self.spec = spec         # … and the object to put there
        self.shell = shell       # for a `commands` entry: the project's own check, run through the shell
        self.needs = list(needs)  # … and the tools it needs on the PATH
        self.tighten = False     # the Stop hook's: lower the baseline to what the code has as it judges

    @property
    def check(self):
        """The check this gate runs: a `gates` entry's name is its own label, not its check."""
        return BY_SECTION[self.section][0] if self.section else self.name.split(":")[0]

    def scope_flags(self, changed):
        """What --changed adds: the changed files for a scoped gate, --changed-only for duplication."""
        if changed is None:
            return []
        if self.check in SCOPED or self.advisory:
            return ["--only"] + changed
        return ["--changed-only"] if self.check in CHANGED_ONLY else []

    def command(self, config_path, strict, changed=None):
        if self.shell:
            return ["sh", "-c", self.shell]
        cmd = [self.script] if self.script.endswith(".sh") else [sys.executable, self.script]
        cmd += ["--config", config_path, "--quiet"] + self.extra
        if strict and self.strict:
            cmd.append("--strict")
        if self.tighten and self.check in TIGHTENS and not self.advisory:
            cmd.append("--tighten")
        return cmd + self.scope_flags(changed)


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


def from_commands(config):
    """One Gate per entry of the `commands` list — the project's own checks, run by this
    runner beside cleat's so one command is the whole preflight: `{"name", "run",
    "needs": [tools], "postflight": bool}`. Exit 0 passes; anything else fails."""
    gates = []
    for entry in config.data.get("commands", []):
        if not entry.get("name") or not entry.get("run"):
            raise KeyError("%s: every \"commands\" entry needs \"name\" and \"run\"; got %r" % (config.file, entry))
        gates.append(Gate(entry["name"], None, False, bool(entry.get("postflight")), shell=entry["run"], needs=entry.get("needs", [])))
    return gates


def configured(config):
    """Every gate this quality.json configures: the sections as sugar, the `gates` list,
    then the project's own `commands`."""
    return from_sections(config) + from_list(config) + from_commands(config)


def gate_config_path(root, name):
    """Where a `gates` entry's own config is written: beside quality.json, so the entry's
    paths resolve against the same directory, and named for this process as well as the
    gate, so two runs at once — a release's preflight and the Stop hook's `--hook` — never
    write, read or remove each other's file."""
    return os.path.join(root, ".cleat-gate-%s.%d.json" % (re.sub(r"[^\w.-]", "_", name), os.getpid()))


def run(gate, config_path, strict, changed=None):
    """Run one gate. A `gates` entry gets a config of its own beside quality.json — the
    check reads its usual section, filled from the entry's `with`, paths relative to the
    same directory — and that file is removed after."""
    root = os.path.dirname(config_path)
    path = config_path
    if gate.section is not None:
        with open(config_path) as handle:
            base = json.load(handle)
        path = gate_config_path(root, gate.name)
        with open(path, "w") as handle:
            json.dump({"project": base.get("project", ""), gate.section: gate.spec}, handle)
    try:
        proc = subprocess.run(gate.command(path, strict, changed), capture_output=True, text=True, cwd=root)
    finally:
        if path != config_path and os.path.exists(path):
            os.remove(path)
    return proc.returncode, (proc.stdout + proc.stderr).rstrip("\n")


def guard_decision(event_text):
    """(tool, target, refused) for a PreToolUse event, or None when it cannot be read."""
    try:
        event = json.loads(event_text or "{}")
    except ValueError:
        return None
    tool_input = event.get("tool_input") or {}
    command = tool_input.get("command") or ""
    target = tool_input.get("file_path") or ""
    return event.get("tool_name"), target or command, refuses(command, target, event.get("cwd"))


def refuses(command, target, cwd=None):
    """Whether a Bash `command` or an edit to `target` would change policy, relative
    paths read from `cwd` (the event's; else this process's)."""
    if shell.runs_with_flag(command, BASELINE_FLAG, BASELINE_FLAG_SHORTEST):
        return True
    cwd = cwd or os.getcwd()
    if target and is_policy(target, cwd):
        return True
    moved = shell.changes_directory(command)
    return any(is_policy(path, cwd, by_name=moved) for path in shell.writes(command))


def is_policy(path, cwd, by_name=False):
    """Whether writing `path` changes policy: it is a quality.json, the agent settings,
    or a policy path of a project it lies in. `by_name` — a relative path after the
    command moved with `cd`, whose directory is unknown — judges the name alone."""
    expanded = os.path.expanduser(path)
    if by_name and not os.path.isabs(expanded):
        return bool(GUARDED_PATH_RE.search(expanded))
    full = os.path.realpath(os.path.join(cwd, expanded))
    if HOOK_SETTINGS_RE.search(full) or os.path.basename(full) == quality_config.FILENAME:
        return True
    return any(PROJECT_POLICY_RE.fullmatch(os.path.relpath(full, top)) for top in projects_above(full))


def projects_above(path):
    """Every directory above `path` that holds a quality.json: the projects it lies in."""
    found, here = [], os.path.dirname(path)
    while True:
        if os.path.isfile(os.path.join(here, quality_config.FILENAME)):
            found.append(here)
        parent = os.path.dirname(here)
        if parent == here:
            return found
        here = parent


def guard(event_text):
    """Exit 2 with a reason when the PreToolUse event on stdin is a command that would
    rewrite policy; 0 otherwise. A malformed event is allowed through — the guard
    refuses what it can read, it does not block the agent on its own bugs."""
    decision = guard_decision(event_text)
    if decision is None:
        return 0
    tool, target, refused = decision
    record_guard(tool, target, refused)
    if not refused:
        return 0
    print("cleat: refused — this would change quality policy (a baseline, quality.json, the gates, or the hooks). "
          "Fix the code the gate names instead; policy changes are made by a person in a reviewed commit.",
          file=sys.stderr)
    return 2


def record_guard(tool, target, refused):
    """A refusal records what was refused; an allowed call records only the tool — a
    command the agent was allowed to run may carry a secret, and it is not this log's
    business. Nothing is recorded when the config says "events": false."""
    root = project_root()
    if not root or not events.enabled(root):
        return
    events.record(root, {"mode": "guard", "verdict": "blocked" if refused else "allowed", "tool": tool,
                         "target": target[:200] if refused else None, "head": events.head(root),
                         "while_red": events.last_hook_failed(root)})


def project_root():
    """The directory of the nearest quality.json, or None — where the event log lives."""
    found = quality_config.find()
    return os.path.dirname(found) if found else None


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


def changed_files(root):
    """The repo-relative files changed against the base — what --changed scopes the heavy
    gates to. Untracked files count; a tree that is not a repository changes nothing."""
    try:
        files = sorted(changed.changed_lines(root, changed.base_ref(root)))
    except changed.ChangedError:
        return []
    return files


def flag_shaped(files):
    """The changed paths that begin with "-" — they would be parsed as FLAGS by the
    scoped checks (which take `--only FILE...`), so a file named `--write-baseline` in
    the change set would rewrite the baselines from the Stop hook, the exact policy
    change the guard exists to refuse. The caller drops the scope and runs the full pass
    instead; the fix is a rename."""
    return [f for f in files if f.startswith("-")]


def _run_one(g, config_path, strict, changed_only):
    """One gate's (code, output), with a project's own command failing on any non-zero exit."""
    code, out = run(g, config_path, strict, changed_only)
    return (1 if g.shell and code else code), out


def run_all(gates, config_path, strict, skip_missing=False, config=None, changed_only=None):
    """Run each gate, print its status row and output; return (failures, every result).
    With `skip_missing`, a gate whose tool is not installed is reported as skipped, not
    run. With `changed_only` (a file list), the scoped gates judge those files only. An
    advisory gate (a CRAP estimate) prints a note when it has one and is neither."""
    failures, results = [], []
    if changed_only is not None:
        print("  changed: %d file(s) against the base — complexity, escapes, conventions and crap judge those; CI judges everything" % len(changed_only))
    judged = only(gates, lambda g: not g.advisory)
    for g in judged:
        result, output = run_gate(g, config_path, strict, skip_missing, config, changed_only)
        results.append(result)
        if output is not None:
            failures.append((g, output))
    for g in only(gates, lambda g: g.advisory):
        run_advisory(g, config_path, config, changed_only)
    print("gate: %d gate(s), %s" % (len(judged), "all passed." if not failures else "%d failed." % len(failures)))
    return failures, results


def run_gate(g, config_path, strict, skip_missing, config, changed_only):
    """(its event result, its output when it failed else None) for one gate, its row printed."""
    absent = missing_tools(g, config) if skip_missing else []
    if absent:
        print("  skip  %s (%s not installed)" % (g.name, ", ".join(absent)))
        return {"name": g.name, "status": "skip", "new": 0, "worsened": 0}, None
    code, out = _run_one(g, config_path, strict, changed_only)
    print("  %s  %s" % (_status(code), g.name))
    for line in out.splitlines():
        print("        " + line)
    return events.gate_result(g.name, code, out), (out if code != 0 else None)


def run_advisory(gate, config_path, config, changed_only):
    """A look ahead: run only when its tools are here, print a row only when it says
    something, and never count as a failure or an event."""
    if missing_tools(gate, config):
        return
    _, out = _run_one(gate, config_path, False, changed_only)
    if out.strip():
        print("  note  %s" % gate.name)
        for line in out.splitlines():
            print("        " + line)


def estimates(gates):
    """For each CRAP gate the preflight leaves out, its `--estimate`: what the postflight
    would say, from the last coverage run on disk, as a note."""
    return [Gate(g.name + " (estimate)", g.script, False, False, g.extra + ["--estimate"], section=g.section,
                 spec=g.spec, advisory=True)
            for g in gates if g.postflight and g.name.split(":")[0] == "crap" and not g.shell]


def _complexity_tool(spec):
    if spec.get("tool"):
        return spec["tool"]
    return "lizard" if spec.get("languages") else "swiftlint"


NEEDS_ASTGREP = {"dead_symbols"}


def _needs_astgrep(section, spec):
    if section in NEEDS_ASTGREP:
        return True
    return section in ("layering", "reachability") and spec.get("references") == "ast-grep"


def tools_for(section, spec):
    """The executables a gate over `spec` (the section's object) needs on the PATH."""
    if not isinstance(spec, dict):
        return []
    if section in ("complexity", "complexity_lizard"):
        return [_complexity_tool(spec)]
    if section == "crap":
        return [_complexity_tool(spec.get("complexity") or {"tool": "swiftlint"})] + (["xcrun"] if "xccov" in spec else [])
    return ["ast-grep"] if _needs_astgrep(section, spec) else []


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
    if gate.shell:
        return [tool for tool in gate.needs if not shutil.which(tool)]
    section, spec = (gate.section, gate.spec) if gate.spec is not None else _spec_of(gate, config)
    return [tool for tool in tools_for(section, spec) if not shutil.which(tool)]


def print_stats(root, since):
    try:
        cutoff = events.parse_since(since) if since else None
    except ValueError as problem:
        return fail(str(problem))
    found = events.read(root, cutoff)
    print("cleat events: %d in %s%s" % (len(found), events.path_for(root), " since %s" % since if since else ""))
    for label, value in events.stats(found):
        print("  %-36s %s" % (label, value))
    return 0


def fail(message):
    print("FAIL: %s" % message, file=sys.stderr)
    return 2


def stop_hook_active():
    """Whether the agent is already continuing because this hook blocked its last stop —
    Claude Code says so in the Stop event on stdin. A hook that blocks again would loop
    the agent forever on a failure it cannot fix."""
    try:
        if sys.stdin.isatty() or not selectors.select([sys.stdin], [], [], 0.5)[0]:
            return False  # nothing piped in: not a Stop event
        return bool(json.loads(sys.stdin.read() or "{}").get("stop_hook_active"))
    except (ValueError, OSError):
        return False


def _last_report_path(root):
    """Where the fingerprint of the last blocked report is kept: beside the run locks,
    under `quality/.running/`, which every consumer already gitignores. This is run
    state, not policy, and losing it only costs one extra report."""
    return os.path.join(root, "quality", runlock.DIRNAME, "hook-last-report") if root else None


def _report_digest(failures):
    """A fingerprint of exactly what the agent would be shown — every gate's name and its
    whole output — so a file fixed, a file broken or a count that moved is a new report."""
    body = "\n".join("%s\n%s" % (g.name, out) for g, out in failures)
    return hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()


def same_as_last_report(failures, root):
    """Whether these failures are the report that already blocked a stop. No memory of
    one — absent, unreadable, no root — means no, so the hook blocks: the safe direction."""
    path = _last_report_path(root)
    if not path:
        return False
    try:
        with open(path) as handle:
            return handle.read().strip() == _report_digest(failures)
    except OSError:
        return False


def remember_report(failures, root):
    """Record the report just sent, so the next stop carrying it can stay quiet. A write
    that fails is not worth a verdict: the hook simply reports once more."""
    path = _last_report_path(root)
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write("%s\n" % _report_digest(failures))
    except OSError:
        pass


def forget_report(root):
    """Green: whatever fails next is news again, and blocks."""
    path = _last_report_path(root)
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def record_hook(root, failures, again, results):
    """One line in the event log per firing; nothing when the project turned the log off."""
    if not root or not events.enabled(root):
        return
    events.record(root, {"mode": "hook", "verdict": "fail" if failures else "pass", "again": again,
                         "head": events.head(root), "changed_files": events.changed_files(root), "gates": results})


def print_failures(failures, again, repeat=False):
    """The failures on stderr, which the harness hands back to the agent — but only once.
    A report already sent is a single line: repeating it buys nothing and costs the agent
    its context every turn. `repeat` separates the two ways a report can be old, because
    a line that claimed sameness on a stop that is merely a continuation would be a lie."""
    if again:
        print("cleat: %d gate(s) still failing (%s) — %s, not blocking again; CI holds the line."
              % (len(failures), ", ".join(g.name for g, _ in failures),
                 "same report as the last blocked stop" if repeat else "this stop is already a continuation"),
              file=sys.stderr)
        return
    print("cleat: %d quality gate(s) failed — fix what each names, then stop again:" % len(failures), file=sys.stderr)
    for g, out in failures:
        print("[%s]\n%s" % (g.name, out), file=sys.stderr)


def finish(failures, hook, root=None, results=()):
    """The exit code — and, in hook mode, the failures on stderr, which is what the
    agent's harness hands back to it. Exit 2 blocks the stop once per distinct failure
    set: a later stop that would send the identical report gets one line and exit 0, so a
    failure the agent cannot fix does not re-send its report every turn. Any change in
    any gate's output — a file fixed, a file broken, a count moved — blocks again, and
    CI holds the line for whatever stays red."""
    if not hook:
        return 1 if failures else 0
    if not failures:
        forget_report(root)
        record_hook(root, failures, False, list(results))
        return 0
    repeat = same_as_last_report(failures, root)
    again = stop_hook_active() or repeat
    record_hook(root, failures, again, list(results))
    print_failures(failures, again, repeat)
    if not again:
        remember_report(failures, root)
    return 0 if again else 2


def scope_of(args, root):
    """The files the heavy gates are scoped to under --changed; None for the full pass. A
    changed path beginning with '-' would parse as a flag in the scoped checks (`--only
    FILE...`), so with one in the change set the scope is dropped and the FULL pass runs,
    said on stderr: nothing is skipped, nothing reaches a check as a flag, and the Stop
    hook keeps its one-block contract (a refusal exiting 2 on every stop trapped the agent
    until the file was renamed, and never reached the event log)."""
    if not args.changed:
        return None
    scope = changed_files(root)
    flagged = flag_shaped(scope or [])
    if not flagged:
        return scope
    sys.stderr.write("gate: %d changed path(s) begin with '-' and would parse as flags in the scoped "
                     "checks; running the full pass instead. Rename them: %s\n"
                     % (len(flagged), " ".join(flagged)))
    return None


def hooks_off():
    """`CLEAT_HOOKS=off`: a session that only reads — a reviewer an autopilot spawned —
    is not the one to hand failures to, and its edits are not the agent's."""
    return os.environ.get("CLEAT_HOOKS", "").lower() in ("off", "0", "false")


def parse_args():
    parser = argparse.ArgumentParser(description="run every gate quality.json configures")
    parser.add_argument("--strict", action="store_true", help="a baseline looser than the code fails too (CI)")
    parser.add_argument("--postflight", action="store_true", help="include the gates that read a coverage run")
    parser.add_argument("--skip-missing-tools", action="store_true",
                        help="report, rather than fail, a gate whose tool (swiftlint, lizard, ast-grep, xcrun) is not installed — for a CI runner that cannot have every tool")
    parser.add_argument("--gate", action="append", help="run only this gate (repeatable)")
    parser.add_argument("--list", action="store_true", help="print the configured gates and exit")
    parser.add_argument("--hook", action="store_true", help="agent Stop hook mode: failures to stderr, exit 2")
    parser.add_argument("--changed", action="store_true",
                        help="scope complexity, escapes, conventions, crap and duplication to the files changed against the base — the fast loop; CI runs the full pass")
    parser.add_argument("--guard", action="store_true", help="agent PreToolUse hook mode: refuse policy-changing commands")
    parser.add_argument("--stats", action="store_true", help="what the hook and the guard did: firings, fail rate, fixes, refusals")
    parser.add_argument("--since", help="with --stats: only events this recent — 7d, 24h, 30m")
    quality_config.add_config_argument(parser)
    return parser.parse_args()


def selected_gates(args, config):
    """The gates to run, or the exit code that ends the run early: an unknown --gate,
    --list, or a config with nothing configured."""
    try:
        gates = select(args, configured(config))
    except KeyError as problem:
        return None, fail(problem.args[0])
    if args.list:
        print("\n".join(g.name for g in gates))
        return None, 0
    if not gates:
        return None, fail("%s configures no gate — see quality/README.md" % config.file)
    if not (args.gate or args.postflight):
        gates = gates + estimates(configured(config))
    return gates, None


def main():
    args = parse_args()
    if (args.guard or args.hook) and hooks_off():
        return 0
    if args.guard:
        return guard(sys.stdin.read())
    config = quality_config.load(args.config)
    if args.stats:
        return print_stats(config.root, args.since)
    gates, early = selected_gates(args, config)
    if gates is None:
        return early
    scope = scope_of(args, config.root)
    for g in gates:
        g.tighten = args.hook
    with runlock.held(os.path.dirname(HERE), "gate.py"):
        failures, results = run_all(gates, config.file, args.strict, args.skip_missing_tools, config, scope)
    return finish(failures, args.hook, config.root, results)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""attach — attach cleat to a project, in one command.

Looks at the tree, writes a `quality.json` for what it finds, writes the
baselines so day one is green, and wires the gates into the places an agent
and a CI run actually pass through. Nothing has to be installed: the tier 0
gates are stdlib Python. With `lizard` on the path the complexity ratchet is
attached too.

  python3 quality/bin/attach.py                          # in a repository that already carries quality/
  python3 …/cleat/quality/bin/attach.py --into ~/proj    # copy quality/ there first, then attach
  python3 quality/bin/attach.py --dry-run                # say what would be written, write nothing
  python3 quality/bin/attach.py --force                  # rewrite a quality.json that already exists
  python3 …/cleat/quality/bin/attach.py --into ~/proj --refresh   # replace a vendored quality/ with this one
  python3 quality/bin/attach.py --add                    # add the gates attach would write that the config lacks
  python3 quality/bin/attach.py --git-hooks              # also a pre-push git hook running the gates
  python3 quality/bin/attach.py --ci                     # also the CI workflow, CODEOWNERS and the ruleset command

What it writes, and why each one:

  quality.json                   the project's facts — languages, documents, ceilings, baselines;
                                 the gates: document ceilings, test hygiene, escapes, duplication,
                                 complexity (with lizard), changed-line coverage (with a report)
  quality/*-baseline.json        the debt that exists today, accepted once; from here it only tightens
  .claude/settings.json          a Stop hook running the gates (a failure is handed back to the agent
                                 as its next task) and a PreToolUse guard refusing commands that
                                 rewrite policy; both merged into whatever is already there
  CLAUDE.md                      a short block telling the agent how the gates work and that a
                                 baseline is not a fix — appended, once, to CLAUDE.md or AGENTS.md
  .git/hooks/pre-push            with --git-hooks: the gates before code leaves the machine, for
                                 whoever works without an agent harness
  .github/workflows/cleat.yml    with --ci: the gates under --strict on every pull request
  .github/CODEOWNERS             with --ci: the control plane — quality.json, the baselines, the
                                 gates, the hooks — needs a person's review

That is local by default: the gates, the baselines and the agent's loop, on
this machine, nothing under .github/. Local gives feedback and the guard;
what it cannot do is stop whoever holds the keyboard from removing the hook.
When there is a second person, or the agent pushes with your keys, `--ci`
adds the workflow and CODEOWNERS and prints the `gh api` command that makes
the check required on the default branch with code-owner review and no
bypass — the one step attach cannot take itself — and what the agent's own
token must lack for that to mean anything.

Every file that already exists is left alone (the settings file is merged),
so attaching twice is safe. `--force` rewrites quality.json and the baselines.

Upgrading a project that already carries a copy: `--refresh` replaces the
template's own files — `bin/`, `tests/`, the two documents — with this
checkout's, and keeps everything else under `quality/` (the baselines, a
project's own additions beside them). `--add` puts the sections attach would
generate today into an existing quality.json where they are missing, writes
their baselines, and touches no section that is already there.
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extractors import patterns
import importlib.util
import runlock

_spec = importlib.util.spec_from_file_location("check_escapes", os.path.join(HERE, "check-escapes.py"))
check_escapes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_escapes)

SKIP_DIRS = set(check_escapes.DEFAULT_SKIP_DIRS) | {"quality", ".claude", ".github", ".idea", ".vscode"}

# The languages attach can recognise: suffixes → the escapes language, and lizard's -l name.
LIZARD_NAMES = {"python": "python", "typescript": "typescript", "javascript": "javascript", "swift": "swift",
                "rust": "rust", "kotlin": "kotlin", "java": "java", "go": "go", "ruby": "ruby"}
SUFFIX_LANGUAGE = {}
for _name, _spec_ in check_escapes.LANGUAGES.items():
    if "alias" in _spec_:
        continue
    for _suffix in _spec_["suffixes"]:
        SUFFIX_LANGUAGE.setdefault(_suffix, _name)
for _suffix in (".js", ".jsx", ".mjs", ".cjs"):
    SUFFIX_LANGUAGE[_suffix] = "javascript"

# A fixed sleep in a test, per language: the one habit every suite grows and every
# suite regrets — the tier 0 hygiene ratchet.
SLEEP_PATTERNS = {
    "python": r"time\.sleep\(",
    "typescript": r"new Promise\([^)]*setTimeout",
    "javascript": r"new Promise\([^)]*setTimeout",
    "swift": r"Task\.sleep\(|\busleep\(|Thread\.sleep\(",
    "rust": r"thread::sleep\(",
    "kotlin": r"Thread\.sleep\(|\bdelay\(",
    "java": r"Thread\.sleep\(",
    "go": r"time\.Sleep\(",
    "ruby": r"\bsleep\b",
    "shell": r"\bsleep\s+[0-9]",
}
TEST_DIR_RE = re.compile(r"^(?:tests?|specs?|__tests__|[A-Za-z]+Tests|testing|e2e)$")
AGENT_DOCS = ["CLAUDE.md", "AGENTS.md", "GEMINI.md", ".github/copilot-instructions.md", ".cursorrules", "README.md"]

AGENT_BLOCK_MARKER = "## Quality gates (cleat)"
AGENT_BLOCK = """
## Quality gates (cleat)

`python3 quality/bin/gate.py` runs every quality gate; it also runs when you
stop, and a failing gate is handed back to you as the next thing to fix. A
failure names the file, the line and what fixes it — split the function, give
the value its real type, make the test pass, handle the error.

Do not edit `quality.json`, anything under `quality/`, or the hooks to make a
gate pass, and do not run `--write-baseline`: the baselines record debt a
person accepted, and only a person loosens them, in a reviewed commit. The
gates only ever tighten; that is the point.
"""

CI_WORKFLOW = """name: cleat
on:
  pull_request:
  push:
    branches: [main, master]
jobs:
  gates:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
%(install)s      - name: quality gates
        # a gate whose tool this runner cannot have (SwiftLint on Linux) is reported as skipped, not failed
        run: python3 quality/bin/gate.py --strict --skip-missing-tools
"""

CODEOWNERS = """# The quality control plane. A change here is a policy decision — a ceiling
# raised, a baseline rewritten, an exemption added, a gate edited — and needs
# a person's approval, not just a green run. With branch protection requiring
# review from code owners, no agent can loosen a gate on its own.
/quality.json          %(owner)s
/quality/              %(owner)s
/.claude/settings.json %(owner)s
/.github/              %(owner)s
"""


class Plan:
    """What attach found and what it will write."""

    def __init__(self, root):
        self.root = root
        self.languages = {}      # language → file count
        self.test_roots = []     # repo-relative
        self.docs = []           # repo-relative
        self.writes = []         # (relative path, what was done)
        self.notes = []
        self.refreshing = False
        self.force = False
        self.ci = False

    def say(self, path, what):
        self.writes.append((path, what))


# ---------------------------------------------------------------- looking

def _note_tests(tests, rel_dir, dirnames):
    for d in dirnames:
        if TEST_DIR_RE.match(d) and rel_dir.count(os.sep) < 3:
            tests.add(os.path.normpath(os.path.join(rel_dir, d)))


def _note_languages(counts, filenames):
    for name in filenames:
        language = SUFFIX_LANGUAGE.get(os.path.splitext(name)[1])
        if language:
            counts[language] = counts.get(language, 0) + 1


def _kept(dirnames):
    return sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))


def _outermost(tests):
    """The test directories that are not inside another found test directory."""
    out = []
    for t in sorted(tests):
        if not any(t != o and t.startswith(o + os.sep) for o in tests):
            out.append(t)
    return out


def survey(plan):
    """Languages by file count, test directories, and agent-facing documents."""
    counts = {}
    tests = set()
    for dirpath, dirnames, filenames in os.walk(plan.root):
        dirnames[:] = _kept(dirnames)
        _note_tests(tests, os.path.relpath(dirpath, plan.root), dirnames)
        _note_languages(counts, filenames)
    plan.languages = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    plan.test_roots = _outermost(tests)
    plan.docs = [d for d in AGENT_DOCS if os.path.isfile(os.path.join(plan.root, d))]


def ceiling_for(words):
    """A document ceiling: today's count with a little room, rounded up to 50."""
    return max(200, int(math.ceil(words * 1.1 / 50.0)) * 50)


def suffixes_of(languages):
    return sorted({s for name in languages for s in check_escapes.language(name)["suffixes"]})


def sleep_count(plan, languages):
    """How many fixed sleeps the test trees hold today — the hygiene ceiling on day one —
    and the regex that counted them; (None, None) when there is no test tree to count in."""
    parts = ["(?:%s)" % SLEEP_PATTERNS[name] for name in languages if name in SLEEP_PATTERNS]
    if not plan.test_roots or not parts:
        return None, None
    regex = "|".join(parts)
    roots = [os.path.join(plan.root, r) for r in plan.test_roots]
    files = patterns.files(roots, suffixes_of(languages), SKIP_DIRS)
    return sum(1 for _ in patterns.sites(files, {"fixed sleeps": regex}, plan.root)), regex


def hygiene_section(plan, languages):
    total, regex = sleep_count(plan, languages)
    if total is None:
        return None
    return {"roots": plan.test_roots, "skip_dirs": [], "extensions": suffixes_of(languages),
            "habits": {"fixed sleeps": {"pattern": regex, "ceiling": total,
                                        "use": "wait for the condition, not the clock"}}}


def complexity_section(plan, languages):
    """The lizard ratchet when lizard is installed; a note on how to reach it when not."""
    lizard_languages = [LIZARD_NAMES[n] for n in languages if n in LIZARD_NAMES]
    if not lizard_languages:
        return None
    if not shutil.which("lizard"):
        plan.notes.append("lizard is not installed, so the complexity ratchet was not attached: "
                          "`pip install lizard`, then run attach again (tier 1).")
        return None
    return {"tool": "lizard", "sources": ["."], "languages": lizard_languages,
            "exclude": ["*/%s/*" % d for d in sorted(SKIP_DIRS)],
            "ceilings": {"cc": 8, "lines": 60}, "baseline": "quality/complexity-baseline.json"}


COVERAGE_REPORT_NAMES = ("lcov.info", "coverage.xml", "cobertura.xml", "cobertura-coverage.xml")


def coverage_report(plan):
    """A coverage report the tests already write, if one is lying in the tree."""
    for dirpath, dirnames, filenames in os.walk(plan.root):
        dirnames[:] = _kept(dirnames)
        for name in filenames:
            if name in COVERAGE_REPORT_NAMES:
                return os.path.relpath(os.path.join(dirpath, name), plan.root)
    return None


def changed_coverage_section(plan):
    report = coverage_report(plan)
    if report is None:
        plan.notes.append("no coverage report found (lcov.info or a Cobertura XML), so changed-line coverage was "
                          "not attached: have the tests write one, then run attach again (tier 2).")
        return None
    return {"report": report, "minimum": 0.8, "min_lines": 20}


BLOCK_WORDS = len(AGENT_BLOCK.split())


def agent_doc(root):
    """The file the agent block goes into: CLAUDE.md or AGENTS.md when present, else CLAUDE.md."""
    return next((d for d in ("CLAUDE.md", "AGENTS.md") if os.path.isfile(os.path.join(root, d))), "CLAUDE.md")


def block_words_pending(root):
    """How many words the agent block will add to its document — 0 when it is already there."""
    return 0 if AGENT_BLOCK_MARKER in _read_if_exists(os.path.join(root, agent_doc(root))) else BLOCK_WORDS


def doc_ceiling(plan, doc):
    """A document's ceiling from today's words — the agent block attach is about to append
    counted in, so attach's own edit cannot trip the gate it installs."""
    words = _words(os.path.join(plan.root, doc))
    if doc == agent_doc(plan.root):
        words += block_words_pending(plan.root)
    return ceiling_for(words)


def xcode_projects(root):
    """Repo-relative project.pbxproj paths of every .xcodeproj in the tree."""
    out = []
    for dirpath, dirnames, _files in os.walk(root):
        dirnames[:] = _kept(dirnames)
        for d in dirnames:
            pbx = os.path.join(dirpath, d, "project.pbxproj")
            if d.endswith(".xcodeproj") and os.path.isfile(pbx):
                out.append(os.path.relpath(pbx, root))
    return out


def manifests_section(plan):
    """A `manifests` entry per Xcode project in the tree, over the test roots beside it —
    a generated project that omits a new test file is the silent kind of failure."""
    roots = [r for r in plan.test_roots if r.endswith("Tests")]
    if not roots:
        return None
    return [{"file": pbx, "roots": roots, "extensions": [".swift"]} for pbx in xcode_projects(plan.root)] or None


def config_for(plan):
    """The tier 0 quality.json (plus complexity when lizard is installed, plus changed-line
    coverage when a report exists) for what the survey found."""
    languages = list(plan.languages)
    config = {"project": os.path.basename(os.path.abspath(plan.root))}
    if plan.docs:
        config["doc_size"] = [{"file": d, "ceiling": doc_ceiling(plan, d)} for d in plan.docs]
    if languages:
        config["escapes"] = {"roots": ["."], "languages": languages, "skip_dirs": ["quality"],
                             "baseline": "quality/escapes-baseline.json"}
        config["duplication"] = {"roots": ["."], "languages": languages, "skip_dirs": ["quality"],
                                 "min_lines": 6, "baseline": "quality/duplication-baseline.json"}
    sections = (("hygiene", hygiene_section(plan, languages)), ("complexity", complexity_section(plan, languages)),
                ("changed_coverage", changed_coverage_section(plan)), ("manifests", manifests_section(plan)))
    for key, section in sections:
        if section is not None:
            config[key] = section
    return config


def _words(path):
    with open(path, errors="replace") as handle:
        return len(handle.read().split())


# ---------------------------------------------------------------- writing

def write_text(plan, rel, text, dry_run, force=False):
    path = os.path.join(plan.root, rel)
    if os.path.exists(path) and not force:
        plan.say(rel, "kept (already there)")
        return False
    plan.say(rel, "written")
    if not dry_run:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)
    return True


TEMPLATE_IGNORE = shutil.ignore_patterns("__pycache__", ".DS_Store", "*-baseline.json", "REFACTOR.md", ".running")
# What --refresh replaces: the template's own files. Everything else under quality/ is the project's.
TEMPLATE_PARTS = ("bin", "tests", "README.md", "STRATEGY.md", "quality.example.json")


class Busy(Exception):
    """A gate or a suite is running from the copy --refresh would replace."""


def _replace_part(source, target, part):
    src, dst = os.path.join(source, part), os.path.join(target, part)
    if not os.path.exists(src):
        return
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    elif os.path.exists(dst):
        os.remove(dst)
    if os.path.isdir(src):
        shutil.copytree(src, dst, ignore=TEMPLATE_IGNORE)
    else:
        shutil.copy2(src, dst)


def refresh(plan, source, target, dry_run):
    """Replace the template's parts of an existing quality/ with this checkout's — unless a
    gate or a suite is running from it right now (--force replaces them anyway)."""
    running = runlock.active(target)
    if running and not plan.force:
        raise Busy("quality/ is in use — %s; a refresh would replace the files it is running. Wait for it, "
                   "or --force to replace them anyway." % ", ".join("%s (pid %d)" % (label, pid) for pid, label in running))
    if running:
        plan.notes.append("--force: replaced quality/ while %d process(es) were running from it" % len(running))
    plan.say("quality/", "refreshed from %s (%s replaced; the rest kept)" % (source, ", ".join(TEMPLATE_PARTS)))
    if not dry_run:
        for part in TEMPLATE_PARTS:
            _replace_part(source, target, part)


def vendor(plan, dry_run, do_refresh=False):
    """Copy the template's quality/ directory into the project when it is not there;
    with --refresh, replace the template's parts of one that is."""
    source = os.path.dirname(HERE)
    target = os.path.join(plan.root, "quality")
    if os.path.realpath(source) == os.path.realpath(target):
        return
    if not os.path.isdir(target):
        plan.say("quality/", "copied from %s" % source)
        if not dry_run:
            shutil.copytree(source, target, ignore=TEMPLATE_IGNORE)
    elif do_refresh:
        refresh(plan, source, target, dry_run)
    else:
        plan.say("quality/", "kept (already there — --refresh replaces the template's files)")


def merge_settings(plan, dry_run):
    """Add the Stop and PreToolUse hooks to .claude/settings.json, keeping what is there."""
    rel = os.path.join(".claude", "settings.json")
    path = os.path.join(plan.root, rel)
    settings = {}
    if os.path.isfile(path):
        with open(path) as handle:
            settings = json.load(handle)
    hooks = settings.setdefault("hooks", {})
    stop = {"hooks": [{"type": "command", "command": "python3 quality/bin/gate.py --hook --changed"}]}
    guard = {"matcher": "Bash|Edit|Write|MultiEdit",
             "hooks": [{"type": "command", "command": "python3 quality/bin/gate.py --guard"}]}
    changed = False
    for event, entry in (("Stop", stop), ("PreToolUse", guard)):
        existing = hooks.setdefault(event, [])
        if not any(json.dumps(e, sort_keys=True) == json.dumps(entry, sort_keys=True) for e in existing):
            existing.append(entry)
            changed = True
    if not changed:
        plan.say(rel, "kept (hooks already wired)")
        return
    plan.say(rel, "hooks added" if os.path.isfile(path) else "written")
    if not dry_run:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            json.dump(settings, handle, indent=2)
            handle.write("\n")


def _read_if_exists(path):
    if not os.path.isfile(path):
        return ""
    with open(path) as handle:
        return handle.read()


def append_agent_block(plan, dry_run):
    """The instructions block, once, in CLAUDE.md or AGENTS.md — CLAUDE.md is created when
    neither exists. Returns the document's name when the block was appended."""
    target = agent_doc(plan.root)
    path = os.path.join(plan.root, target)
    existing = _read_if_exists(path)
    if AGENT_BLOCK_MARKER in existing:
        plan.say(target, "kept (block already there)")
        return None
    plan.say(target, "block appended" if existing else "written")
    if not dry_run:
        with open(path, "a") as handle:
            handle.write(("\n" if existing and not existing.endswith("\n") else "") + AGENT_BLOCK)
    return target


def raise_ceiling_for_block(plan, config, config_path, doc, dry_run):
    """An existing doc_size entry for the document the block went into is raised by the
    block's words in the same write, and the report says so — attach's edit must not be
    the first thing its own gate fails on."""
    for entry in config.get("doc_size", []):
        if entry.get("file") == doc and _words(os.path.join(plan.root, doc)) > int(entry.get("ceiling", 0)):
            entry["ceiling"] = int(entry["ceiling"]) + BLOCK_WORDS
            plan.say("quality.json", "doc_size: %s ceiling raised by %d for the cleat block" % (doc, BLOCK_WORDS))
            if not dry_run:
                with open(config_path, "w") as handle:
                    json.dump(config, handle, indent=2)
                    handle.write("\n")


PRE_PUSH = """#!/bin/sh
# cleat: the quality gates before code leaves the machine. Weaker than CI (--no-verify
# exists) but seconds long, for whoever works without an agent harness.
exec python3 quality/bin/gate.py
"""


def write_git_hook(plan, dry_run):
    """A pre-push hook running the gates, when the project is a git repository and no
    hook of someone else's is already there."""
    hooks = os.path.join(plan.root, ".git", "hooks")
    if not os.path.isdir(os.path.join(plan.root, ".git")):
        plan.notes.append("--git-hooks: not a git repository, so no pre-push hook was written")
        return
    path = os.path.join(hooks, "pre-push")
    existing = _read_if_exists(path)
    if existing and "cleat" not in existing:
        plan.say(".git/hooks/pre-push", "kept (a hook of yours is already there — add `python3 quality/bin/gate.py` to it)")
        return
    plan.say(".git/hooks/pre-push", "kept (already cleat's)" if existing else "written")
    if not dry_run and not existing:
        os.makedirs(hooks, exist_ok=True)
        with open(path, "w") as handle:
            handle.write(PRE_PUSH)
        os.chmod(path, 0o755)


def origin_repo(root):
    """`owner/name` from the origin remote's GitHub path, or None."""
    proc = subprocess.run(["git", "-C", root, "remote", "get-url", "origin"], capture_output=True, text=True)
    match = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", proc.stdout.strip()) if proc.returncode == 0 else None
    return "%s/%s" % (match.group(1), match.group(2)) if match else None


RULESET = """{
  "name": "cleat", "target": "branch", "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "bypass_actors": [],
  "rules": [
    {"type": "pull_request", "parameters": {"required_approving_review_count": 1, "require_code_owner_review": true,
      "dismiss_stale_reviews_on_push": true, "require_last_push_approval": true, "required_review_thread_resolution": false}},
    {"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": true,
      "required_status_checks": [{"context": "%(check)s"}]}},
    {"type": "non_fast_forward"},
    {"type": "deletion"}
  ]
}"""
CHECK_NAME = "gates"   # the job name in the generated workflow, which is the status check's context


def ruleset_command(root):
    """The `gh api` call that makes the CI check required on the default branch, with
    code-owner review and no bypass — filled in for this repository."""
    repo = origin_repo(root) or "OWNER/REPO"
    return "gh api -X POST repos/%s/rulesets --input - <<'EOF'\n%s\nEOF" % (repo, RULESET % {"check": CHECK_NAME})


def owner_handle(root):
    """`@owner` from the origin remote's GitHub path, else a placeholder to fill in."""
    proc = subprocess.run(["git", "-C", root, "remote", "get-url", "origin"], capture_output=True, text=True)
    match = re.search(r"github\.com[:/]([^/]+)/", proc.stdout) if proc.returncode == 0 else None
    return "@" + match.group(1) if match else "@OWNER  # ← your GitHub handle or team"


def write_baselines(plan, config, dry_run, only=None):
    """Run each baselined gate's --write-baseline, so day one is green — for every
    baselined section in `config`, or only those named in `only`."""
    baselined = (("escapes", "check-escapes.py"), ("duplication", "check-duplication.py"),
                 ("complexity", "check-complexity.py"))
    for section, script in baselined:
        if section not in config or (only is not None and section not in only):
            continue
        rel = config[section]["baseline"]
        plan.say(rel, "written (today's debt, accepted once)")
        if dry_run:
            continue
        proc = subprocess.run([sys.executable, os.path.join(plan.root, "quality", "bin", script), "--write-baseline",
                               "--config", os.path.join(plan.root, "quality.json")], capture_output=True, text=True)
        if proc.returncode != 0:
            plan.notes.append("%s could not write its baseline: %s" % (script, (proc.stdout + proc.stderr).strip()))


def add_sections(plan, existing, generated, config_path, dry_run):
    """The sections attach would write today that `existing` lacks, merged in and their
    baselines written; what is already there is not touched."""
    missing = [k for k in generated if k not in existing and k != "project"]
    if not missing:
        plan.say("quality.json", "kept (every gate attach would add is already there)")
        return existing
    merged = dict(existing)
    for key in missing:
        merged[key] = generated[key]
    plan.say("quality.json", "added: " + ", ".join(missing))
    if not dry_run:
        with open(config_path, "w") as handle:
            json.dump(merged, handle, indent=2)
            handle.write("\n")
    write_baselines(plan, merged, dry_run, only=missing)
    return merged


RETIRED_COMPLEXITY_KEYS = ("cwd", "config")


def migrate_config(plan, existing, config_path, dry_run):
    """Rewrite what --refresh knows is retired: a `complexity` section in the old
    SwiftLint-native shape becomes the one gate's shape — from a `complexity_lizard`
    beside it when there is one, else as `tool: swiftlint` over the same sources with
    a baseline still to write."""
    old = existing.get("complexity")
    if not isinstance(old, dict) or not any(k in old for k in RETIRED_COMPLEXITY_KEYS):
        return existing
    migrated = dict(existing)
    if isinstance(existing.get("complexity_lizard"), dict):
        migrated["complexity"] = {"tool": "lizard", **existing["complexity_lizard"]}
        del migrated["complexity_lizard"]
        plan.say("quality.json", "complexity: the retired SwiftLint-native section replaced by complexity_lizard, under its new name")
    else:
        cwd = old.get("cwd", ".")
        sources = old.get("sources", [])
        migrated["complexity"] = {"tool": "swiftlint", "sources": [os.path.normpath(os.path.join(cwd, s)) for s in ([sources] if isinstance(sources, str) else sources)],
                                  "ceilings": {"cc": 8, "lines": 60}, "baseline": "quality/complexity-baseline.json"}
        plan.say("quality.json", "complexity: the retired SwiftLint-native section rewritten as tool: swiftlint")
        plan.notes.append("the SwiftLint baseline was in SwiftLint's own format: write the new one with "
                          "`quality/bin/check-complexity.py --write-baseline` (on a machine with swiftlint).")
    if not dry_run:
        with open(config_path, "w") as handle:
            json.dump(migrated, handle, indent=2)
            handle.write("\n")
    return migrated


def settle_config(plan, root, dry_run, force, add):
    """The quality.json the rest of attach works from: written fresh, kept, or added to."""
    generated = config_for(plan)
    config_path = os.path.join(root, "quality.json")
    if not os.path.isfile(config_path) or force:
        write_text(plan, "quality.json", json.dumps(generated, indent=2) + "\n", dry_run, force=True)
        write_baselines(plan, generated, dry_run)
        return generated
    with open(config_path) as handle:
        existing = json.load(handle)
    if plan.refreshing:
        existing = migrate_config(plan, existing, config_path, dry_run)
    if add:
        return add_sections(plan, existing, generated, config_path, dry_run)
    plan.say("quality.json", "kept (already there — --add merges missing gates, --force rewrites it)")
    return existing


IGNORED = ("quality/.running/", "quality/.events.jsonl")


def ignore_runtime_files(plan, dry_run):
    """The run registry and the event log are runtime state, not policy: gitignored."""
    path = os.path.join(plan.root, ".gitignore")
    existing = _read_if_exists(path)
    missing = [entry for entry in IGNORED if entry not in existing.splitlines()]
    if not missing:
        plan.say(".gitignore", "kept (runtime files already ignored)")
        return
    plan.say(".gitignore", "added " + ", ".join(missing))
    if not dry_run:
        with open(path, "a") as handle:
            handle.write(("" if not existing or existing.endswith("\n") else "\n") + "\n".join(missing) + "\n")


def attach(root, dry_run, force, do_refresh=False, add=False, git_hooks=False, ci=False):
    plan = Plan(root)
    plan.refreshing = do_refresh
    plan.force = force
    vendor(plan, dry_run, do_refresh)
    survey(plan)
    config = settle_config(plan, root, dry_run, force, add)
    merge_settings(plan, dry_run)
    ignore_runtime_files(plan, dry_run)
    appended = append_agent_block(plan, dry_run)
    if appended and not dry_run:
        raise_ceiling_for_block(plan, config, os.path.join(root, "quality.json"), appended, dry_run)
    if git_hooks:
        write_git_hook(plan, dry_run)
    if ci:
        lizard_step = "      - run: pip install lizard\n" if "complexity" in config else ""
        write_text(plan, os.path.join(".github", "workflows", "cleat.yml"), CI_WORKFLOW % {"install": lizard_step}, dry_run)
        write_text(plan, os.path.join(".github", "CODEOWNERS"), CODEOWNERS % {"owner": owner_handle(root)}, dry_run)
    plan.ci = ci
    return plan, config


def _found_lines(plan, config):
    lines = []
    if plan.languages:
        lines.append("languages: " + ", ".join("%s (%d files)" % kv for kv in plan.languages.items()))
    if plan.test_roots:
        lines.append("test trees: " + ", ".join(plan.test_roots))
    gates = [k for k in ("doc_size", "hygiene", "escapes", "duplication", "complexity", "changed_coverage", "manifests") if k in config]
    lines.append("gates: " + (", ".join(gates) if gates else "none — the tree has nothing attach recognises"))
    return lines


def summary_lines(plan, config):
    lines = _found_lines(plan, config)
    for rel, what in plan.writes:
        lines.append("%-32s %s" % (rel, what))
    for note in plan.notes:
        lines.append("note: " + note)
    return lines


def print_next(plan):
    """What attach cannot do itself. Local by default: run it, and know what the hooks
    give. With --ci: the ruleset, and the identity the agent needs for it to hold."""
    print("Next:")
    if not plan.ci:
        print("  1. Run python3 quality/bin/gate.py. Every gate is green today; from here they only tighten.")
        print("  2. The Stop hook hands a failing gate back to the agent; the guard refuses edits to the policy.")
        print("     What local cannot do is stop whoever holds the keyboard from removing the hook.")
        print("  3. When there is a reviewer, or the agent pushes with your keys: attach.py --ci adds the workflow,")
        print("     CODEOWNERS and the command that makes the check required. quality/README.md has the tiers.")
        return
    print("  1. Commit. Then make the check required on the default branch, with code-owner review and no bypass:")
    print("     " + ruleset_command(plan.root).replace("\n", "\n     "))
    print("  2. Give the agent its own GitHub identity — a machine user or an App with contents and pull-request")
    print("     write, no admin. A PR's author cannot approve it, so an agent that is you leaves nobody to approve;")
    print("     an agent that holds admin can turn the rules off. With its own identity, you are the reviewer.")
    print("  3. quality/README.md: the tiers above this one, and how each attachment point holds.")


def report(plan, config, dry_run):
    print("cleat %s %s" % ("would attach to" if dry_run else "attached to", plan.root))
    for line in summary_lines(plan, config):
        print("  " + line)
    if not dry_run:
        print_next(plan)


def main():
    parser = argparse.ArgumentParser(description="attach cleat's quality gates to a project")
    parser.add_argument("--into", help="the project to attach to (default: the repository this quality/ is in)")
    parser.add_argument("--dry-run", action="store_true", help="say what would be written; write nothing")
    parser.add_argument("--force", action="store_true", help="rewrite quality.json and the baselines")
    parser.add_argument("--refresh", action="store_true", help="replace a vendored quality/'s template files with this checkout's")
    parser.add_argument("--add", action="store_true", help="add the gates attach would write that an existing quality.json lacks")
    parser.add_argument("--git-hooks", action="store_true", help="also write a pre-push git hook that runs the gates")
    parser.add_argument("--ci", action="store_true", help="also write the CI workflow and CODEOWNERS, and print the ruleset command")
    args = parser.parse_args()
    root = os.path.abspath(args.into) if args.into else os.path.dirname(os.path.dirname(HERE))
    if not os.path.isdir(root):
        print("FAIL: no such directory: %s" % root, file=sys.stderr)
        return 2
    try:
        plan, config = attach(root, args.dry_run, args.force, args.refresh, args.add, args.git_hooks, args.ci)
    except Busy as problem:
        print("FAIL: %s" % problem, file=sys.stderr)
        return 2
    report(plan, config, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""test-attach — assert quality/bin/attach.py over a throwaway project.

A git repository with Python and TypeScript sources carrying a few escapes, a
tests/ tree with a fixed sleep, a README and a CLAUDE.md: --dry-run writes
nothing and says what it would; attach --into copies quality/ there, writes a
quality.json naming the languages, the documents with ceilings, the test tree
with today's sleep count as the ceiling, and the escapes baseline; the gates
then pass under --strict; the Stop and PreToolUse hooks are in
.claude/settings.json, merged with what was there; the agent block is appended
to CLAUDE.md once; the workflow and CODEOWNERS exist, the latter naming the
origin's owner with --ci and nothing under .github without; attaching again changes nothing; --refresh replaces the
template's files and keeps the project's; --add merges the gates a config
lacks and writes their baselines; and a new escape then fails the gates in
hook mode. Writes nothing outside a temporary directory.

  python3 quality/tests/test-attach.py
"""
import json, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "attach.py")
suite = Suite("test-attach"); check = suite.check



def read(path):
    with open(path) as handle:
        return handle.read()


def run(*args):
    proc = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def gate(root, *args):
    proc = subprocess.run([sys.executable, os.path.join(root, "quality", "bin", "gate.py"), *args],
                          capture_output=True, text=True, cwd=root)
    return proc.returncode, proc.stdout, proc.stderr


tmp = tempfile.mkdtemp(prefix="attach-")
try:
    root = os.path.join(tmp, "proj")
    write(os.path.join(root, "src", "app.py"), "import os\n\nx = os.getcwd()  # type: ignore\n")
    write(os.path.join(root, "web", "index.ts"), "const a: any = 1;\n")
    write(os.path.join(root, "web", "util.js"), "// eslint-disable\nexport const b = 1;\n")
    write(os.path.join(root, "tests", "test_app.py"), "import time\n\ndef test_x():\n    time.sleep(1)\n")
    write(os.path.join(root, "node_modules", "dep", "index.ts"), "const z: any = 1;\n")
    write(os.path.join(root, "README.md"), " ".join(["word"] * 120) + "\n")
    write(os.path.join(root, "CLAUDE.md"), "# Project\n\nBe careful.\n")
    write(os.path.join(root, ".claude", "settings.json"), json.dumps({"permissions": {"allow": ["Bash(ls:*)"]}}))
    subprocess.run(["git", "-C", root, "init", "-q"], check=True)
    subprocess.run(["git", "-C", root, "remote", "add", "origin", "git@github.com:acme-org/proj.git"], check=True)

    code, out = run("--into", root, "--dry-run")
    check("--dry-run succeeds and says what it would write", code == 0 and "would attach" in out and "quality.json" in out, out)
    check("--dry-run writes nothing", not os.path.exists(os.path.join(root, "quality.json")) and not os.path.isdir(os.path.join(root, "quality")), out)

    code, out = run("--into", root)
    check("attach succeeds", code == 0, out)
    check("by default nothing is written under .github, and the next steps are local", not os.path.exists(os.path.join(root, ".github")) and "Run python3 quality/bin/gate.py" in out and "attach.py --ci" in out, out)
    code, out_first = run("--into", root, "--ci")
    check("--ci adds the workflow and CODEOWNERS on top", code == 0 and os.path.isfile(os.path.join(root, ".github", "CODEOWNERS")), out_first)
    check("quality/ is copied into the project", os.path.isfile(os.path.join(root, "quality", "bin", "gate.py")) and os.path.isfile(os.path.join(root, "quality", "README.md")), out)
    check("without the template's own plan or baselines", not os.path.exists(os.path.join(root, "quality", "REFACTOR.md"))
          and "quality/bin" not in read(os.path.join(root, "quality", "escapes-baseline.json")), out)
    config = json.load(open(os.path.join(root, "quality.json")))
    check("the languages are detected by file count, node_modules ignored", config["escapes"]["languages"] == ["python", "javascript", "typescript"], str(config.get("escapes")))
    check("the documents get ceilings with a little room", {d["file"]: d["ceiling"] for d in config["doc_size"]} == {"CLAUDE.md": 200, "README.md": 200}, str(config.get("doc_size")))
    check("the test tree is found and today's sleep count is the ceiling", config["hygiene"]["roots"] == ["tests"] and config["hygiene"]["habits"]["fixed sleeps"]["ceiling"] == 1, str(config.get("hygiene")))
    check("the escapes baseline is written", os.path.isfile(os.path.join(root, "quality", "escapes-baseline.json")), out)
    check("duplication is attached with its density baseline", "duplication" in config and os.path.isfile(os.path.join(root, "quality", "duplication-baseline.json")), out)
    check("without a coverage report, changed-line coverage is a note, not a gate", "changed_coverage" not in config and "tier 2" in out, out)
    if shutil.which("lizard"):
        check("with lizard installed the complexity ratchet is attached", "complexity" in config and os.path.isfile(os.path.join(root, "quality", "complexity-baseline.json")), out)
    else:
        check("without lizard the note says how to reach tier 1", "pip install lizard" in out, out)

    code, gout, gerr = gate(root, "--strict")
    check("the gates pass on day one, under --strict", code == 0 and "all passed" in gout, gout + gerr)

    settings = json.load(open(os.path.join(root, ".claude", "settings.json")))
    check("the existing settings are kept", settings["permissions"]["allow"] == ["Bash(ls:*)"], str(settings))
    stop = json.dumps(settings["hooks"].get("Stop"))
    pre = json.dumps(settings["hooks"].get("PreToolUse"))
    check("the Stop hook runs the gates in hook mode", "gate.py --hook" in stop, stop)
    check("the run registry and the event log are gitignored", all(e in read(os.path.join(root, ".gitignore")) for e in ("quality/.running/", "quality/.events.jsonl")), read(os.path.join(root, ".gitignore")))
    check("the PreToolUse guard is wired for the tools that change files", "gate.py --guard" in pre and "Bash" in pre and "Edit" in pre, pre)
    claude_md = read(os.path.join(root, "CLAUDE.md"))
    check("the agent block is appended to CLAUDE.md, after what was there", claude_md.startswith("# Project") and "## Quality gates (cleat)" in claude_md and "--write-baseline" in claude_md, claude_md)
    check("the workflow runs the gates under --strict", "gate.py --strict" in read(os.path.join(root, ".github", "workflows", "cleat.yml")))
    codeowners = read(os.path.join(root, ".github", "CODEOWNERS"))
    check("CODEOWNERS names the origin's owner over the control plane", "@acme-org" in codeowners and "/quality.json" in codeowners and "/quality/" in codeowners, codeowners)

    before = {p: read(os.path.join(root, p)) for p in ("quality.json", "CLAUDE.md", ".claude/settings.json", ".github/CODEOWNERS")}
    code, out = run("--into", root)
    after = {p: read(os.path.join(root, p)) for p in before}
    check("attaching again changes nothing", code == 0 and before == after and "kept" in out, out)
    check("and the agent block is not appended twice", after["CLAUDE.md"].count("## Quality gates (cleat)") == 1)

    # ---- --refresh: the template's files replaced, the project's kept
    vendored_gate = os.path.join(root, "quality", "bin", "gate.py")
    write(vendored_gate, "# an older copy\n")
    write(os.path.join(root, "quality", "bin", "check-retired.py"), "# a script the template no longer ships\n")
    write(os.path.join(root, "quality", "notes.md"), "the project's own notes\n")
    code, out = run("--into", root, "--refresh")
    check("--refresh replaces the template's files", code == 0 and "refreshed" in out and read(vendored_gate) == read(os.path.join(os.path.dirname(HERE), "bin", "gate.py")), out)
    check("and drops what the template no longer ships", not os.path.exists(os.path.join(root, "quality", "bin", "check-retired.py")), out)
    check("and keeps the baselines, the config and the project's own files",
          os.path.isfile(os.path.join(root, "quality", "escapes-baseline.json")) and os.path.isfile(os.path.join(root, "quality", "notes.md"))
          and read(os.path.join(root, "quality.json")) == before["quality.json"], out)

    # ---- --refresh refuses while a gate or suite is running from the copy; a stale pid does not count
    running_dir = os.path.join(root, "quality", ".running")
    write(os.path.join(running_dir, str(os.getpid())), "gate.py\n")
    code, out = run("--into", root, "--refresh")
    check("--refresh refuses while a registered process is alive, naming it", code == 2 and "in use" in out and "gate.py (pid %d)" % os.getpid() in out, out)
    os.remove(os.path.join(running_dir, str(os.getpid())))
    write(os.path.join(running_dir, "999999"), "stale\n")
    code, out = run("--into", root, "--refresh")
    check("a pid that is gone is stale: cleared, and the refresh proceeds", code == 0 and "refreshed" in out and not os.path.exists(os.path.join(running_dir, "999999")), out)

    # ---- --refresh migrates a retired SwiftLint-native complexity section
    kept = json.load(open(os.path.join(root, "quality.json")))
    retired = dict(kept); retired["complexity"] = {"cwd": "ios", "config": ".swiftlint.yml", "baseline": ".swiftlint-baseline.json", "sources": ["App", "Core"]}
    write(os.path.join(root, "quality.json"), json.dumps(retired, indent=2) + "\n")
    code, out = run("--into", root, "--refresh")
    migrated = json.load(open(os.path.join(root, "quality.json")))
    check("--refresh rewrites the retired shape as tool: swiftlint over the same sources, and says the baseline must be written",
          code == 0 and migrated["complexity"]["tool"] == "swiftlint" and migrated["complexity"]["sources"] == ["ios/App", "ios/Core"] and "write the new one" in out, out + str(migrated.get("complexity")))
    retired["complexity_lizard"] = kept["complexity"] if "complexity" in kept else {"sources": ["."], "languages": ["python"], "ceilings": {"cc": 8, "lines": 60}, "baseline": "quality/complexity-baseline.json"}
    write(os.path.join(root, "quality.json"), json.dumps(retired, indent=2) + "\n")
    code, out = run("--into", root, "--refresh")
    migrated = json.load(open(os.path.join(root, "quality.json")))
    check("with a complexity_lizard beside it, --refresh keeps that one under the new name and drops the retired section",
          code == 0 and migrated["complexity"].get("tool") == "lizard" and "complexity_lizard" not in migrated and "under its new name" in out, out)
    write(os.path.join(root, "quality.json"), json.dumps(kept, indent=2) + "\n")

    # ---- --add: missing gates merged into an existing config, the rest untouched
    trimmed = json.load(open(os.path.join(root, "quality.json")))
    del trimmed["duplication"]; del trimmed["escapes"]
    trimmed["doc_size"][0]["ceiling"] = 999
    write(os.path.join(root, "quality.json"), json.dumps(trimmed, indent=2) + "\n")
    os.remove(os.path.join(root, "quality", "duplication-baseline.json"))
    code, out = run("--into", root, "--add", "--dry-run")
    check("--add --dry-run says what it would add and writes nothing", code == 0 and "added: escapes, duplication" in out and "duplication" not in json.load(open(os.path.join(root, "quality.json"))), out)
    code, out = run("--into", root, "--add")
    added = json.load(open(os.path.join(root, "quality.json")))
    check("--add merges the missing gates", code == 0 and "escapes" in added and "duplication" in added, out)
    check("and writes their baselines", os.path.isfile(os.path.join(root, "quality", "duplication-baseline.json")), out)
    check("and leaves an existing section exactly as it was", added["doc_size"][0]["ceiling"] == 999, str(added["doc_size"]))
    code, out = run("--into", root, "--add")
    check("--add with nothing missing changes nothing", code == 0 and "every gate attach would add is already there" in out, out)
    subprocess.run([sys.executable, os.path.join(root, "quality", "bin", "check-escapes.py"), "--write-baseline"], capture_output=True, cwd=root)

    write(os.path.join(root, "src", "app.py"), "import os\n\nx = os.getcwd()  # type: ignore\ny = 1  # noqa\n")
    code, gout, gerr = gate(root, "--hook")
    check("a new escape then fails the gates in hook mode, with the site on stderr", code == 2 and "src/app.py:4  noqa" in gerr, gout + gerr)

    # ---- the agent block is counted toward its document's ceiling, so attach's edit cannot trip attach's gate
    tight = os.path.join(tmp, "tight")
    write(os.path.join(tight, "src", "a.py"), "x = 1\n")
    write(os.path.join(tight, "CLAUDE.md"), " ".join(["word"] * 180) + "\n")
    subprocess.run(["git", "-C", tight, "init", "-q"], check=True)
    code, out = run("--into", tight)
    tight_config = json.load(open(os.path.join(tight, "quality.json")))
    ceiling = next(d["ceiling"] for d in tight_config["doc_size"] if d["file"] == "CLAUDE.md")
    words = len(read(os.path.join(tight, "CLAUDE.md")).split())
    check("a fresh config's ceiling for the agent's document counts the block attach appends", code == 0 and ceiling >= words > 200, "ceiling %s, words %s" % (ceiling, words))
    code, gout, gerr = gate(tight, "--strict")
    check("so day one is green on a document that was near its ceiling", code == 0, gout + gerr)
    existing = os.path.join(tmp, "existing")
    write(os.path.join(existing, "src", "a.py"), "x = 1\n")
    write(os.path.join(existing, "CLAUDE.md"), " ".join(["word"] * 190) + "\n")
    write(os.path.join(existing, "quality.json"), json.dumps({"project": "existing", "doc_size": [{"file": "CLAUDE.md", "ceiling": 200}]}))
    subprocess.run(["git", "-C", existing, "init", "-q"], check=True)
    code, out = run("--into", existing)
    raised = json.load(open(os.path.join(existing, "quality.json")))["doc_size"][0]["ceiling"]
    check("an existing config's ceiling is raised by the block's words in the same write, and the report says so",
          code == 0 and raised > 200 and "ceiling raised by" in out, out)
    code, gout, gerr = gate(existing, "--strict")
    check("and that project's gate is green too", code == 0, gout + gerr)

    # ---- a generated Xcode project gets a manifests gate over the test roots
    ios = os.path.join(tmp, "ios")
    write(os.path.join(ios, "App.xcodeproj", "project.pbxproj"), "// !$*UTF8*$!\n\t\tA1 /* StoreTests.swift */ = {path = StoreTests.swift;};\n")
    write(os.path.join(ios, "AppTests", "StoreTests.swift"), "final class StoreTests {}\n")
    write(os.path.join(ios, "App", "Thing.swift"), "struct Thing {}\n")
    subprocess.run(["git", "-C", ios, "init", "-q"], check=True)
    code, out = run("--into", ios)
    ios_config = json.load(open(os.path.join(ios, "quality.json")))
    check("an .xcodeproj beside a *Tests root gets a manifests gate", code == 0 and ios_config.get("manifests") == [{"file": "App.xcodeproj/project.pbxproj", "roots": ["AppTests"], "extensions": [".swift"]}], str(ios_config.get("manifests")))
    code, gout, gerr = gate(ios, "--strict", "--skip-missing-tools")
    check("and it is green on day one", code == 0, gout + gerr)

    # ---- the closing lines: the ruleset command filled in, and the identity advice
    check("attach ends with the gh ruleset command for this repository's origin, naming the check",
          "gh api -X POST repos/acme-org/proj/rulesets" in out_first and '"context": "gates"' in out_first and "require_code_owner_review" in out_first, out_first)
    check("and says the agent needs its own identity", "own GitHub identity" in out_first, out_first)

    # ---- --git-hooks: a pre-push hook, once, and never over someone else's
    code, out = run("--into", root, "--git-hooks")
    hook = os.path.join(root, ".git", "hooks", "pre-push")
    check("--git-hooks writes an executable pre-push hook that runs the gates", code == 0 and os.access(hook, os.X_OK) and "gate.py" in read(hook), out)
    code, out = run("--into", root, "--git-hooks")
    check("and keeps it on a second run", "kept (already cleat's)" in out, out)
    write(hook, "#!/bin/sh\necho mine\n")
    code, out = run("--into", root, "--git-hooks")
    check("a hook of someone else's is kept, with the line to add", read(hook) == "#!/bin/sh\necho mine\n" and "add `python3 quality/bin/gate.py`" in out, out)

    code, out = run("--into", os.path.join(tmp, "missing"))
    check("a directory that does not exist is refused", code == 2, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

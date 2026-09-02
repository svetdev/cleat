#!/usr/bin/env python3
"""test-report-hotspots — assert the churn reader, the ranking and the config in
quality/bin/report-hotspots.py.

Driven over a throwaway git repo with real commits at real dates: a hot complex
file (touched often, inside the window), a cold complex file (equally complex,
its only commit outside the window) and a hot simple file (touched often, low
complexity). Only the hot complex file's function should top the list — the
cold one's churn is 0 despite matching complexity, and the simple one's score
stays low despite matching churn. Complexity comes from a fabricated SwiftLint
report via --lint, exactly as check-crap.py's own test drives it, so this
starts no real lint run. Then the same repo under a fixture quality.json with
only --config: the window and the lint roots come from the file. Starts no
build, writes nothing outside a temporary directory.

  quality/tests/test-report-hotspots.py
"""
import importlib.util, json, os, shutil, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "report-hotspots.py")
spec = importlib.util.spec_from_file_location("report_hotspots", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failed = 0


def check(name, ok, detail=""):
    global failed
    print("  %s  %s" % ("ok  " if ok else "FAIL", name) + ("" if ok else "\n          " + detail))
    failed += 0 if ok else 1


def write(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as h:
        h.write(data if isinstance(data, str) else json.dumps(data))


def iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def git(repo, *args, date=None):
    env = dict(os.environ)
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, "git %s failed: %s" % (" ".join(args), proc.stderr)
    return proc.stdout


def commit(repo, path, days_ago, message):
    with open(path, "a") as h:
        h.write("// touch\n")
    git(repo, "add", path)
    git(repo, "commit", "-q", "-m", message, date=iso(days_ago))


tmp = tempfile.mkdtemp(prefix="report-hotspots-")
try:
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")

    services = os.path.join(repo, "Services")
    hot = os.path.join(services, "Hot.swift")
    cold = os.path.join(services, "Cold.swift")
    simple = os.path.join(services, "SimpleHot.swift")
    write(hot, "import Foundation\n\nfunc hot(_ a: Int) -> Int { a }\n")
    write(cold, "import Foundation\n\nfunc cold(_ a: Int) -> Int { a }\n")
    write(simple, "import Foundation\n\nfunc simpleHot(_ a: Int) -> Int { a }\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init", date=iso(200))

    # `git log --since` walks history newest-first and prunes once a commit falls
    # outside the window — reliable only when each commit's date is not older than
    # the one before it in the graph. Commits below are created oldest-first, in one
    # globally decreasing days-ago sequence, so that invariant holds throughout.
    #
    # hot complex: 5 commits, comfortably inside even a narrow window
    # cold complex: no commit but "init" (200 days ago) — outside every window used below
    # hot simple: 3 commits, low complexity, at offsets close enough together that a
    #   narrowed window (15 days) still catches every one of them, while it drops all
    #   but the newest of hot's — showing the window narrows churn per file, not globally
    for i in (50, 40, 30, 20, 10):
        commit(repo, hot, i, "touch hot %d" % i)
    for i in (9, 6, 3):
        commit(repo, simple, i, "touch simple %d" % i)

    lint = [
        {"file": hot, "line": 3, "reason": "Function should have complexity 1 or less; currently complexity is 9"},
        {"file": cold, "line": 3, "reason": "Function should have complexity 1 or less; currently complexity is 9"},
        {"file": simple, "line": 3, "reason": "Function should have complexity 1 or less; currently complexity is 2"},
    ]
    lint_path = os.path.join(tmp, "lint.json")
    write(lint_path, lint)

    nowhere = os.path.join(tmp, "nowhere")
    os.makedirs(nowhere)

    def run(*args, cwd=nowhere):
        p = subprocess.run([sys.executable, SCRIPT, "--lint", lint_path, "--repo", repo,
                             "--window-days", "90", *args], capture_output=True, text=True, cwd=cwd)
        return p.returncode, p.stdout + p.stderr

    code, out = run()
    check("a fully flagged run succeeds and opens no quality.json", code == 0 and "quality.json" not in out, out)
    check("all three functions are measured", "3 function(s) measured" in out, out)
    check("two of the three were touched in the window", "2 touched in the last 90 day(s)" in out, out)
    check("with no floor configured or given, the header names no floor at all", "held back" not in out, out)
    lines = [l for l in out.splitlines() if "hotspot" in l]
    check("the hot complex function tops the list", "Hot.swift:3" in lines[0], out)
    check("its score is churn x cc", "hotspot 45 (churn 5 x cc 9)" in lines[0], out)

    # the same ranking from a saved lizard run, for the stacks SwiftLint does not read
    rows = [(1, 9, 5, 1, 1, "hot@3-3@%s" % hot, hot, "hot", "hot ( a )", 3, 3),
            (1, 9, 5, 1, 1, "cold@3-3@%s" % cold, cold, "cold", "cold ( a )", 3, 3),
            (1, 2, 5, 1, 1, "simpleHot@3-3@%s" % simple, simple, "simpleHot", "simpleHot ( a )", 3, 3)]
    csv_path = os.path.join(tmp, "lizard.csv")
    with open(csv_path, "w") as handle:
        handle.write("\n".join(",".join('"%s"' % c if isinstance(c, str) else str(c) for c in row) for row in rows) + "\n")
    p = subprocess.run([sys.executable, SCRIPT, "--lizard-csv", csv_path, "--repo", repo, "--window-days", "90"],
                       capture_output=True, text=True, cwd=nowhere)
    csv_out = p.stdout + p.stderr
    csv_lines = [l for l in csv_out.splitlines() if "hotspot" in l]
    check("--lizard-csv measures from a saved lizard run", p.returncode == 0 and "3 function(s) measured" in csv_out, csv_out)
    check("and ranks the same way", "Hot.swift:3" in csv_lines[0] and "hotspot 45 (churn 5 x cc 9)" in csv_lines[0], csv_out)

    # with lizard installed, a config that measures with lizard measures with lizard — --sources or not —
    # and --tool lizard --languages needs no config at all; without lizard, neither may crash into SwiftLint
    lizard_config = os.path.join(tmp, "lizard-quality.json")
    write(lizard_config, {"complexity": {"tool": "lizard", "sources": ["repo/Services"], "languages": ["swift"],
                                         "ceilings": {"cc": 8, "lines": 60}, "baseline": "b.json"}})
    for extra in ((), ("--sources", services)):
        p = subprocess.run([sys.executable, SCRIPT, "--config", lizard_config, "--repo", repo, "--window-days", "90", *extra],
                           capture_output=True, text=True, cwd=nowhere)
        text = p.stdout + p.stderr
        if shutil.which("lizard"):
            check("a lizard config measures with lizard%s" % (" even with --sources" if extra else ""), p.returncode == 0 and "function(s) measured" in text, text)
        else:
            check("without lizard a lizard config fails naming lizard, not swiftlint%s" % (" even with --sources" if extra else ""), p.returncode == 2 and "lizard" in text and "swiftlint" not in text, text)
    p = subprocess.run([sys.executable, SCRIPT, "--sources", services, "--tool", "lizard", "--languages", "swift", "--repo", repo, "--window-days", "90"],
                       capture_output=True, text=True, cwd=nowhere)
    text = p.stdout + p.stderr
    check("--tool lizard --languages measures with no config at all (or names lizard when it is missing)",
          (p.returncode == 0 and "measured" in text) if shutil.which("lizard") else (p.returncode == 2 and "lizard" in text), text)
    check("the hot simple function ranks above the cold complex one", "SimpleHot.swift:3" in lines[1], out)
    check("its score is its own churn x cc, not the complex file's", "hotspot 6 (churn 3 x cc 2)" in lines[1], out)
    check("the cold complex function sinks to the bottom despite matching complexity",
          "Cold.swift:3" in lines[2] and "hotspot 0 (churn 0 x cc 9)" in lines[2], out)

    code, out = run("--top", "1")
    check("--top limits how many are printed", out.count("hotspot ") == 1 and "Hot.swift:3" in out, out)
    check("the measured/touched counts still cover everything, not just what's shown",
          "3 function(s) measured, 2 touched" in out, out)

    code, out = run("--top", "0")
    check("--top 0 fails rather than silently printing nothing", code == 2 and "--top must be positive" in out, out)

    code, out = run("--min-cc", "8")
    check("--min-cc 8 drops the simple function (cc 2) from the ranking entirely",
          "SimpleHot.swift" not in out, out)
    check("the hot complex function (cc 9) still tops the list", "Hot.swift:3" in out, out)
    check("the cold complex function (cc 9) still clears the floor", "Cold.swift:3" in out, out)
    check("measured and touched counts still cover every function, not only the ranked ones",
          "3 function(s) measured, 2 touched in the last 90 day(s)" in out, out)
    check("the header says how many rows the floor held back", "1 held back by the cc 8 floor" in out, out)
    check("top counts only the rows that cleared the floor", "top 2 by churn x complexity" in out, out)

    # ---- the same repo, every fact from a fixture quality.json and only --config
    config = os.path.join(repo, "quality.json")
    write(config, {"hotspots": {"window_days": 15}, "crap": {"sources": ["Services"]}})

    def run_config(*args, cwd=repo):
        p = subprocess.run([sys.executable, SCRIPT, "--config", config, "--lint", lint_path, *args],
                            capture_output=True, text=True, cwd=cwd)
        return p.returncode, p.stdout + p.stderr

    code, out = run_config()
    check("--config alone: window_days narrows to 15, so only 1 of hot's 5 commits (day 10) counts",
          code == 0 and "hotspot 9 (churn 1 x cc 9)" in out, out)
    check("--config alone: simple's 3 commits (days 9, 6, 3) all still fall inside 15",
          "hotspot 6 (churn 3 x cc 2)" in out, out)

    code, out = run_config("--window-days", "90")
    check("--window-days overrides the config's window_days", "hotspot 45 (churn 5 x cc 9)" in out, out)

    # min_cc floor from a fixture quality.json
    write(config, {"hotspots": {"min_cc": 8}, "crap": {"sources": ["Services"]}})
    code, out = run_config()
    check("--config alone: hotspots.min_cc floors the ranking, dropping SimpleHot (cc 2)",
          code == 0 and "SimpleHot.swift" not in out, out)
    check("Hot and Cold (cc 9) still clear the configured floor", "Hot.swift:3" in out and "Cold.swift:3" in out, out)
    check("the header says how many the configured floor held back", "1 held back by the cc 8 floor" in out, out)

    code, out = run_config("--min-cc", "0")
    check("--min-cc overrides the config's min_cc", "SimpleHot.swift:3" in out and "held back" not in out, out)

    # a quality.json with no "hotspots" section at all: window_days defaults to 90, min_cc to 0, not an error
    write(config, {"crap": {"sources": ["Services"]}})
    code, out = run_config()
    check("an absent hotspots section defaults window_days to 90, rather than failing",
          code == 0 and "hotspot 45 (churn 5 x cc 9)" in out, out)
    check("an absent hotspots section defaults min_cc to 0, holding nothing back",
          "held back" not in out, out)

    # the config is found by walking up from the working directory
    p = subprocess.run([sys.executable, SCRIPT, "--lint", lint_path], capture_output=True, text=True, cwd=repo)
    check("without --config the nearest quality.json above the working directory is used, and --repo defaults to its directory",
          p.returncode == 0 and "hotspot 45 (churn 5 x cc 9)" in p.stdout, p.stdout + p.stderr)

    # with --lint given, a run needs no "crap" section at all — not even for sources
    write(config, {})
    code, out = run_config("--lint", lint_path)
    check("with --lint given, a run needs no crap section at all", code == 0, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failed:
    print("test-report-hotspots: %d case(s) failed." % failed)
    sys.exit(1)
print("test-report-hotspots: all cases passed.")

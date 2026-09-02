#!/usr/bin/env python3
"""test-conformance — one fixture per language, asserting known answers.

Each tree under quality/tests/fixtures/<language>/ carries a file with a
known set of escape sites, decoys included (a word containing "any", a bang
inside a string, a comment that is still a site because escapes live in
comments), and one `branchy` function of cyclomatic 5. The escapes gate must
find exactly the expected kinds, and — when lizard is installed — the
complexity reader must report branchy at cyclomatic 5 for every language it
parses. A language whose numbers drift under a new tool version fails here
before a project's baseline silently changes. Writes only under a temporary
directory.

  python3 quality/tests/test-conformance.py
"""
import json, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write
BIN = os.path.join(os.path.dirname(HERE), "bin")
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, BIN)
from extractors import complexity

suite = Suite("test-conformance"); check = suite.check


# language → (expected escape kinds with counts, lizard -l name or None)
EXPECTED = {
    "python": ({"noqa": 1, "type ignore": 2, "bare except": 1}, "python"),
    "typescript": ({"any": 2, "ts-ignore": 1, "non-null assertion": 1, "eslint-disable": 1, "skipped test": 1}, "typescript"),
    "swift": ({"force try": 1, "force cast": 1, "force unwrap": 1, "swiftlint:disable": 1, "unchecked Sendable": 1}, "swift"),
    "rust": ({"allow": 1, "unwrap": 1, "expect": 1, "unsafe": 1, "todo": 1, "skipped test": 1}, "rust"),
    "go": ({"nolint": 1, "skipped test": 1}, "go"),
    "kotlin": ({"suppress": 1, "not-null assertion": 1, "skipped test": 1}, "kotlin"),
    "java": ({"suppress warnings": 1, "skipped test": 1}, "java"),
    "ruby": ({"rubocop:disable": 1, "skipped test": 1}, "ruby"),
    "shell": ({"errors ignored": 2, "shellcheck disable": 1}, None),
}

tmp = tempfile.mkdtemp(prefix="conformance-")
try:
    for language, (expected, lizard_name) in EXPECTED.items():
        config = os.path.join(tmp, language + ".json")
        with open(config, "w") as handle:
            json.dump({"escapes": {"roots": [os.path.join(FIXTURES, language)], "languages": [language],
                                   "baseline": os.path.join(tmp, language + "-baseline.json")}}, handle)
        proc = subprocess.run([sys.executable, os.path.join(BIN, "check-escapes.py"), "--config", config], capture_output=True, text=True)
        found = {}
        for line in proc.stdout.splitlines():
            parts = line.strip().split("  ")
            if len(parts) >= 3 and ":" in parts[0] and not line.startswith("FAIL"):
                kind = parts[1]
                count = 1
                if " x" in kind:
                    kind, times = kind.rsplit(" x", 1)
                    count = int(times)
                found[kind] = found.get(kind, 0) + count
        check("%s: exactly the expected escape sites, decoys excluded" % language, found == expected, "found %r, expected %r\n%s" % (found, expected, proc.stdout))

        if lizard_name and shutil.which("lizard"):
            try:
                text = complexity.run_lizard([os.path.join(FIXTURES, language)], [lizard_name], [], [])
            except complexity.ToolError as problem:
                check("%s: lizard parses the fixture" % language, False, str(problem))
                continue
            functions, _ = complexity.functions_from_csv(text)
            branchy = [f for f in functions if f.name.endswith("branchy")]
            check("%s: lizard reports branchy at cyclomatic 5" % language, len(branchy) == 1 and branchy[0].cc == 5,
                  str([(f.name, f.cc) for f in functions]))
        elif lizard_name:
            check("%s: lizard is not installed, so complexity is not checked" % language, True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

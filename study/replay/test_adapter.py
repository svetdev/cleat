#!/usr/bin/env python3
"""test_adapter — adapter.py and doctor.py on a made-up Python project with two commits.

  python3 study/replay/test_adapter.py

The project's runner is its own unittest wrapper that writes JUnit XML, so nothing beyond
Python is needed. Writes only under a temporary directory.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import adapter              # noqa: E402
import doctor               # noqa: E402
import lang as languages    # noqa: E402
import mutate               # noqa: E402
import textsites            # noqa: E402

RUNNER = '''import sys, unittest
from xml.sax.saxutils import quoteattr
files, out = sys.argv[1:-1], sys.argv[-1]
loader, cases = unittest.TestLoader(), []
for f in files:
    mod = f[:-3].replace("/", ".")
    for test in loader.loadTestsFromName(mod):
        for case in test:
            r = unittest.TestResult(); case.run(r)
            cls, name = case.id().rsplit(".", 1)
            bad = "<failure/>" if r.failures or r.errors else ""
            cases.append("<testcase classname=%s name=%s>%s</testcase>" % (quoteattr(cls), quoteattr(name), bad))
open(out, "w").write("<testsuites><testsuite>%s</testsuite></testsuites>" % "".join(cases))
'''
SPEC = {"name": "python", "languages": ["python"], "install": "true",
        "test": "python3 run_tests.py {files} {out}", "manifests": ["pyproject.toml"],
        "agent_commands": ["python3 run_tests.py *"], "tests": ["tests/*.py"]}
BASE = {"run_tests.py": RUNNER, "tests/__init__.py": "", "acme/__init__.py": "",
        "acme/price.py": "def total(xs):\n    return sum(xs)\n",
        "tests/test_price.py": "import unittest\nfrom acme.price import total\n\n"
                               "class Total(unittest.TestCase):\n    def test_sum(self):\n        self.assertEqual(total([1, 2]), 3)\n"}
CHANGE = {"acme/price.py": "def total(xs, discount=0):\n    if discount > 0:\n        return sum(xs) - discount\n"
                           "    return sum(xs)\n",
          "tests/test_price.py": BASE["tests/test_price.py"] + "\n    def test_discount(self):\n"
                                 "        self.assertEqual(total([5], discount=2), 3)\n"}


def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=True).stdout.strip()


def write(root, files):
    for rel, text in files.items():
        os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
        with open(os.path.join(root, rel), "w") as handle:
            handle.write(text)


def project(tmp):
    """(clones dir, repo name, adapter path, the second commit's sha and parent)."""
    clones = os.path.join(tmp, "clones")
    root = os.path.join(clones, "acme__price")
    os.makedirs(root)
    git(root, "init", "-q")
    for files, message in ((BASE, "base"), (CHANGE, "Add a discount to total (#2)")):
        write(root, files)
        git(root, "add", "-A")
        git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", message)
    path = os.path.join(tmp, "replay.json")
    with open(path, "w") as handle:
        json.dump(SPEC, handle)
    return clones, "acme__price", path, git(root, "rev-parse", "HEAD"), git(root, "rev-parse", "HEAD^")


class Reading(unittest.TestCase):
    def spec(self, tmp, **changes):
        path = os.path.join(tmp, "replay.json")
        with open(path, "w") as handle:
            json.dump({k: v for k, v in dict(SPEC, **changes).items() if v is not None}, handle)
        return path

    def test_refuses_missing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SystemExit, "fill in install"):
                adapter.read(self.spec(tmp, install=None))

    def test_refuses_a_test_command_without_a_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SystemExit, "JUnit XML to {out}"):
                adapter.read(self.spec(tmp, test="pytest {files}"))

    def test_refuses_no_languages_and_no_globs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SystemExit, "name its languages"):
                adapter.read(self.spec(tmp, languages=None))

    def test_lang_loads_an_adapter_by_path_and_refuses_an_unknown_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(languages.get(self.spec(tmp)).label, "python")
        with self.assertRaisesRegex(SystemExit, "write a replay.json"):
            languages.get("cobol")


class Rules(unittest.TestCase):
    def test_default_rules_come_from_cleat(self):
        production, is_test = adapter.rules({"languages": ["python"]})
        self.assertTrue(production("acme/price.py"))
        self.assertTrue(is_test("tests/test_price.py"))
        self.assertFalse(production("tests/test_price.py"))
        self.assertFalse(production(".venv/lib/x.py"))
        self.assertFalse(production("README.md"))

    def test_globs_override(self):
        production, is_test = adapter.rules({"production": ["lib/**"], "tests": ["spec/**"]})
        self.assertTrue(production("lib/a.rb"))
        self.assertFalse(production("spec/a_spec.rb"))
        self.assertTrue(is_test("spec/a_spec.rb"))


class Junit(unittest.TestCase):
    def test_class_names_map_to_files_and_skips_are_left_out(self):
        xml = ('<testsuites><testsuite>'
               '<testcase classname="tests.test_price.Total" name="test_sum"/>'
               '<testcase classname="tests.test_price.Total" name="test_off"><failure/></testcase>'
               '<testcase classname="tests.test_price.Total" name="test_later"><skipped/></testcase>'
               '<testcase file="spec/a_spec.rb" classname="A" name="works"><error/></testcase>'
               '</testsuite></testsuites>')
        got = adapter.parse_junit(xml, ["tests/test_price.py"])
        self.assertEqual(got, {"tests/test_price.py::tests.test_price.Total::test_sum": True,
                               "tests/test_price.py::tests.test_price.Total::test_off": False,
                               "spec/a_spec.rb::A::works": False})

    def test_a_broken_report_reads_as_no_tests(self):
        self.assertEqual(adapter.parse_junit("<testsuites", []), {})

    def test_check_errors_drop_positions(self):
        out = "src/a.py:12:5: error: Incompatible types\nsrc/a.py(3,4): error TS2322: x\nall good"
        self.assertEqual(adapter.check_errors(out), ["src/a.py: error TS2322: x", "src/a.py: error: Incompatible types"])


class OnAProject(unittest.TestCase):
    def test_doctor_reads_the_suite(self):
        with tempfile.TemporaryDirectory() as tmp:
            clones, repo, path, _, _ = project(tmp)
            said = io.StringIO()
            with contextlib.redirect_stdout(said):
                code = doctor.main(path, os.path.join(clones, repo))
            self.assertEqual(code, 0, said.getvalue())
            self.assertIn("suite: 2 tests read, 2 pass, 0 fail", said.getvalue())
            self.assertIn("ready for a replay", said.getvalue())

    def test_doctor_says_what_to_fix_when_no_test_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            clones, repo, path, _, _ = project(tmp)
            with open(path, "w") as handle:
                json.dump(dict(SPEC, test="true {files} {out}"), handle)
            said = io.StringIO()
            with contextlib.redirect_stdout(said):
                code = doctor.main(path, os.path.join(clones, repo))
            self.assertEqual(code, 1)
            self.assertIn("no test read: the test command must run {files}", said.getvalue())

    def test_validate_finds_the_fail_to_pass_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            clones, repo, path, sha, parent = project(tmp)
            lang = adapter.load(path)
            cand = dict(repo=repo, sha=sha, parent=parent,
                        files=[("M", None, "acme/price.py"), ("M", None, "tests/test_price.py")])
            task, why = lang.validate(clones, os.path.join(tmp, "work"), cand)
            self.assertEqual(why, "ok")
            self.assertEqual(task["f2p"], ["tests/test_price.py::tests.test_price.Total::test_discount"])
            self.assertEqual(task["p2p"], ["tests/test_price.py::tests.test_price.Total::test_sum"])



class Sites(unittest.TestCase):
    def test_spaced_operators_only_and_never_in_comments_or_strings(self):
        rust = 'fn f(v: Vec<T>) -> bool { println!("a < b"); if a < b && !c { x + 1 } else { y -= 1; true } } // a == b'
        self.assertEqual([s[2:] for s in textsites.in_file("a.rs", rust)],
                         [("<", "<="), ("&&", "||"), ("+", "-"), ("true", "false"), ("!", "")])

    def test_python_words(self):
        py = 'if a == b and not c:  # x < y\n    return True\ns = "a + b"\nn = -1 + x'
        self.assertEqual([s[2:] for s in textsites.in_file("a.py", py)],
                         [("==", "!="), ("+", "-"), ("True", "False"), ("and", "or"), ("not ", "")])

    def test_type_mutants_stay_typescript_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, path, _, _ = project(tmp)
            with self.assertRaisesRegex(SystemExit, "TypeScript only"):
                mutate.all_sites(adapter.load(path), tmp, [], types=True)


class Mutants(unittest.TestCase):
    def test_mutants_on_the_written_lines_are_killed_or_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            clones, repo, path, sha, _ = project(tmp)
            snapshot, work = os.path.join(clones, repo), os.path.join(tmp, "work")
            plan = dict(lang=path, repo=repo, changes=[dict(index=1, sha=sha)])
            with contextlib.redirect_stdout(io.StringIO()):
                mutate.one(adapter.load(path), snapshot, plan, 1, work, "plain-1", snapshot, "own", 50, 1)
            with open(os.path.join(work, "mutants", "1", "plain-1-own.jsonl")) as handle:
                rows = sorted((r["old"], r["new"], r["status"]) for r in map(json.loads, handle))
            self.assertEqual(rows, [("-", "+", "killed"), (">", ">=", "survived")])


if __name__ == "__main__":
    unittest.main()

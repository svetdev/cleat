"""harness — what every guard suite under quality/tests shares.

A suite is a script: it builds a throwaway tree, drives the real check over
it, and asserts on the exit code and the text. The three things each one
needs — a `check` that prints ok/FAIL and counts, a `write` that creates the
parent directories, a `run` that captures a script's output — live here so a
suite is its cases and nothing else.

  from harness import Suite, write, run
  suite = Suite("test-check-thing"); check = suite.check
  …
  suite.finish()
"""

import os
import subprocess
import sys


class Suite:
    def __init__(self, name):
        self.name = name
        self.failed = 0

    def check(self, name, ok, detail=""):
        print("  %s  %s" % ("ok  " if ok else "FAIL", name) + ("" if ok else "\n          " + detail))
        self.failed += 0 if ok else 1

    def finish(self):
        print("%s: %s" % (self.name, "all passed." if self.failed == 0 else "%d case(s) failed." % self.failed))
        sys.exit(1 if self.failed else 0)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)


def run(script, *args, cwd=None, stdin=None):
    """(exit code, stdout + stderr) of `script` run under this interpreter."""
    proc = subprocess.run([sys.executable, script, *args], capture_output=True, text=True, cwd=cwd, input=stdin,
                          stdin=subprocess.DEVNULL if stdin is None else None)
    return proc.returncode, proc.stdout + proc.stderr

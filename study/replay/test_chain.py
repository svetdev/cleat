#!/usr/bin/env python3
"""test_chain — chain.py's oracle gate (no agent chain starts before an oracle has resolved
90% of the scored changes it would run to), its refusal to run a cleat chain whose attach failed,
and the tree copy every script makes, cloned on APFS and copied elsewhere.

  python3 study/replay/test_chain.py

Writes only under a temporary directory.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import adapter        # noqa: E402
import chain          # noqa: E402
import draw           # noqa: E402
import test_adapter   # noqa: E402


def work_dir(tmp, scored=10, unscored=2):
    """A work dir whose plan has `scored` scored changes, then `unscored` more."""
    changes = [dict(index=i + 1, scored=i < scored) for i in range(scored + unscored)]
    with open(os.path.join(tmp, "plan.json"), "w") as handle:
        json.dump(dict(changes=changes), handle)
    return tmp


def oracle(work, resolved, name="oracle-1", check="check_full"):
    """Steps 1..len(resolved) of an oracle chain, each resolved or not by the full check."""
    for i, ok in enumerate(resolved, 1):
        out = os.path.join(work, "chains", name, "steps", "%03d" % i)
        os.makedirs(out)
        with open(os.path.join(out, "score.json"), "w") as handle:
            json.dump({check: dict(resolved=ok)}, handle)


class OracleGate(unittest.TestCase):
    def test_refuses_agents_with_no_oracle(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SystemExit, "the best has 0. Run it alone first: chain.py <clones dir> .* 10 oracle-1"):
                chain.oracle_gate(work_dir(tmp), 10, ["plain-1", "cleat-1"])

    def test_refuses_agents_below_ninety_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = work_dir(tmp)
            oracle(work, [True] * 8 + [False] * 2)
            with self.assertRaisesRegex(SystemExit, "at least 90% of the 10 scored changes through change 10 before plain-1 run; the best has 8"):
                chain.oracle_gate(work, 10, ["plain-1"])

    def test_counts_only_the_full_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = work_dir(tmp)
            oracle(work, [True] * 10, check="check")
            with self.assertRaises(SystemExit):
                chain.oracle_gate(work, 10, ["cleat-1"])

    def test_lets_agents_run_at_ninety_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = work_dir(tmp)
            oracle(work, [True] * 9 + [False])
            chain.oracle_gate(work, 10, ["plain-1", "cleat-1"])

    def test_judges_only_the_changes_the_agents_run_to(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = work_dir(tmp)
            oracle(work, [True] * 5)
            chain.oracle_gate(work, 5, ["plain-1"])
            with self.assertRaisesRegex(SystemExit, "the best has 5"):
                chain.oracle_gate(work, 10, ["plain-1"])

    def test_the_best_oracle_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = work_dir(tmp)
            oracle(work, [False] * 10, name="oracle-1")
            oracle(work, [True] * 10, name="oracle-2")
            chain.oracle_gate(work, 10, ["plain-1"])

    def test_an_oracle_alone_always_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            chain.oracle_gate(work_dir(tmp), 10, ["oracle-1"])



class CopyTree(unittest.TestCase):
    def tree(self, tmp):
        source = os.path.join(tmp, "source")
        os.makedirs(os.path.join(source, "pkg"))
        with open(os.path.join(source, "pkg", "a.txt"), "w") as handle:
            handle.write("a")
        os.symlink("pkg/a.txt", os.path.join(source, "link"))
        return source

    def check_copy(self, platform):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = self.tree(tmp), os.path.join(tmp, "target")
            with mock.patch.object(draw.sys, "platform", platform):
                draw.copy_tree(source, target)
            self.assertEqual(open(os.path.join(target, "pkg", "a.txt")).read(), "a")
            self.assertEqual(os.readlink(os.path.join(target, "link")), "pkg/a.txt")

    def test_clones_on_a_mac(self):
        self.check_copy(sys.platform)

    def test_copies_where_it_cannot_clone(self):
        self.check_copy("linux")


class Attach(unittest.TestCase):
    def test_a_failed_attach_stops_the_cleat_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            clones, repo, path, _, parent = test_adapter.project(tmp)
            fake = os.path.join(tmp, "cleat")
            os.makedirs(os.path.join(fake, "quality", "bin"))
            with open(os.path.join(fake, "quality", "bin", "attach.py"), "w") as handle:
                handle.write("import sys\nprint('attach: no test command found')\nsys.exit(1)\n")
            plan = dict(repo=repo, changes=[dict(index=1, parent=parent)])
            with mock.patch.object(chain, "CLEAT", fake):
                with self.assertRaisesRegex(SystemExit, "(?s)cleat-1: cleat.s attach failed.*no test command found"):
                    chain.setup(adapter.load(path), plan, clones, tmp, "cleat-1", os.path.join(tmp, "repo"))


if __name__ == "__main__":
    unittest.main()

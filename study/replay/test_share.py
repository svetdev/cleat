#!/usr/bin/env python3
"""test_share — share.py's cards: what they hold, what check refuses, and that nothing in a work
dir that names the code reaches one.

  python3 study/replay/test_share.py

Writes only under a temporary directory.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import share   # noqa: E402

SECRET = "acmesecret"


def dump(path, value, lines=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        if lines:
            handle.write("".join(json.dumps(v) + "\n" for v in value))
        else:
            json.dump(value, handle)


def step(resolved, lines=1000, over8=2, cost=0.5):
    check = dict(resolved=resolved, missing=0)
    return dict(measure=dict(lines=lines, over8=over8), check=check, check_full=check,
                run=dict(total_cost_usd=cost), stray=[], files=["%s/billing.py" % SECRET])


def work_dir(tmp, oracle_resolved=2):
    """Two scored changes, a checkpoint at 2, an oracle, one tree per arm, and mutants, every
    one of them naming the code: repo, paths, commit text, commands."""
    work = os.path.join(tmp, "work")
    spec = dict(name="python", languages=["python"], install="uv sync", manifests=["uv.lock"],
                test="uv run --project /home/%s pytest {files} --junitxml={out}" % SECRET, agent_commands=["uv *"])
    dump(os.path.join(tmp, "replay.json"), spec)
    changes = [dict(index=i, sha="%040d" % i, scored=True, text="Fix %s invoices" % SECRET) for i in (1, 2)]
    dump(os.path.join(work, "plan.json"), dict(lang=os.path.join(tmp, "replay.json"), repo="acme__" + SECRET,
                                               changes=changes, probes={"2": []}))
    for chain, ok in (("oracle-1", [True, oracle_resolved == 2]), ("plain-1", [True, False]), ("cleat-1", [True, True])):
        dump(os.path.join(work, "chains", chain, "setup.json"),
             dict(cleat="f" * 40, measure=dict(lines=12345), recipe=dict(cmd="uv sync --directory /home/" + SECRET)))
        for i, resolved in enumerate(ok, 1):
            dump(os.path.join(work, "chains", chain, "steps", "%03d" % i, "score.json"), step(resolved))
    rows = [dict(id=0, file="%s/billing.py" % SECRET, at=3, old="<", new="<=", status="killed"),
            dict(id=1, file="%s/billing.py" % SECRET, at=9, old="+", new="-", status="survived")]
    for tree in ("plain-1", "cleat-1", "upstream"):
        dump(os.path.join(work, "mutants", "2", "%s-own.jsonl" % tree), rows, lines=True)
    return work


class Card(unittest.TestCase):
    def test_a_card_names_nothing_of_the_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = share.card(work_dir(tmp))
            self.assertEqual(share.check(got), [])
            self.assertNotIn(SECRET, json.dumps(got))
            self.assertNotIn("home", json.dumps(got))

    def test_a_card_carries_the_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = share.card(work_dir(tmp))
            self.assertEqual((got["language"], got["runner"], got["size"]), ("python", "pytest", "10k to 20k"))
            self.assertEqual(got["oracle"], dict(scored=2, resolved=2))
            self.assertEqual((got["arms"]["plain"]["resolved"], got["arms"]["cleat"]["resolved"]), (1, 2))
            self.assertEqual(got["arms"]["cleat"]["density"], {"2": 2.0})
            self.assertEqual(got["mutants"]["2/written/own"], {"plain": [[1, 1]], "cleat": [[1, 1]], "upstream": [[1, 1]]})
            self.assertEqual(got["status"], "complete")

    def test_a_run_that_stopped_is_shared_as_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = work_dir(tmp)
            os.remove(os.path.join(work, "chains", "plain-1", "steps", "002", "score.json"))
            got = share.card(work)
            self.assertEqual(got["status"], "stopped")
            self.assertEqual(share.check(got), [])

    def test_the_id_is_random(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = work_dir(tmp)
            self.assertNotEqual(share.card(work)["id"], share.card(work)["id"])

    def test_writing_refuses_a_broken_oracle(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "oracle resolved 1 of 2"):
                    share.write_card(work_dir(tmp, oracle_resolved=1))


class Check(unittest.TestCase):
    def good(self):
        with tempfile.TemporaryDirectory() as tmp:
            return share.card(work_dir(tmp))

    def refused(self, card, reason):
        self.assertTrue(any(reason in p for p in share.check(card)), share.check(card))

    def test_refuses_a_field_outside_the_schema(self):
        card = self.good()
        card["repo"] = "acme__billing"
        self.refused(card, "card.repo is not a field a card may hold")

    def test_refuses_a_nested_field_outside_the_schema(self):
        card = self.good()
        card["arms"]["cleat"]["files"] = ["src/a.py"]
        self.refused(card, "card.arms.cleat.files is not a field")

    def test_refuses_an_exact_size(self):
        card = self.good()
        card["size"] = "12345"
        self.refused(card, "size is not a bucket")

    def test_refuses_a_long_note(self):
        card = self.good()
        card["note"] = "x" * 281
        self.refused(card, "note is over 280")

    def test_refuses_a_command_as_runner(self):
        card = self.good()
        card["runner"] = "uv run pytest tests/billing"
        self.refused(card, "runner is not a known kind")

    def test_refuses_text_where_a_number_goes(self):
        card = self.good()
        card["probes"] = {"2/visible/plain": dict(runs=1, resolved=1, cost="acme")}
        self.refused(card, "cost is not a number")

    def test_files_must_be_named_by_their_id(self):
        card = self.good()
        with tempfile.TemporaryDirectory() as tmp:
            right, wrong = os.path.join(tmp, card["id"] + ".json"), os.path.join(tmp, "acme-billing.json")
            for path in (right, wrong):
                dump(path, card)
            with contextlib.redirect_stdout(io.StringIO()) as said:
                self.assertEqual(share.check_files([right]), 0)
                self.assertEqual(share.check_files([right, wrong]), 1)
            self.assertIn("not named <id>.json", said.getvalue())


class Pieces(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual([share.bucket(n) for n in (0, 1999, 2000, 49999, 50000, 10 ** 6)],
                         ["under 2k", "under 2k", "2k to 5k", "20k to 50k", "50k to 100k", "over 100k"])

    def test_runner_kind_never_the_command(self):
        self.assertEqual(share.runner("cd /srv/acme && npx jest --ci {files}"), "jest")
        self.assertEqual(share.runner("./acme-test-runner {files} {out}"), "other")


if __name__ == "__main__":
    unittest.main()

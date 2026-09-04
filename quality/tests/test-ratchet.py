#!/usr/bin/env python3
"""test-ratchet — assert the engine every baseline gate shares, quality/bin/ratchet.py.

The five outcomes over hand-made findings and entries: new, worsened on any
ratcheted value, improved, held, stale; a value the entry never recorded is not
compared; both baseline file shapes read; provenance drift by tool, version and
config; and the report's exit codes — 1 on new or worse, 0 on loose, 1 on loose
under --strict — with the accept command printed only where it can only
tighten. Writes nothing outside a temporary directory.

  python3 quality/tests/test-ratchet.py
"""
import contextlib, io, json, os, shutil, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import ratchet

suite = Suite("test-ratchet"); check = suite.check


F = ratchet.Finding
findings = [F("a.py", 3, "def a():", {"cc": 9, "lines": 10}),      # new
            F("b.py", 3, "def b():", {"cc": 12, "lines": 10}),     # worsened on cc
            F("c.py", 3, "def c():", {"cc": 9, "lines": 70}),      # worsened on lines
            F("d.py", 3, "def d():", {"cc": 9, "lines": 10}),      # improved
            F("e.py", 3, "def e():", {"cc": 9, "lines": 10}),      # held
            F("f.py", 3, "def f():", {"cc": 30, "lines": 10})]     # held: entry never recorded cc
entries = [{"file": "b.py", "text": "def b():", "cc": 9, "lines": 10},
           {"file": "c.py", "text": "def c():", "cc": 9, "lines": 61},
           {"file": "d.py", "text": "def d():", "cc": 17, "lines": 10},
           {"file": "e.py", "text": "def e():", "cc": 9, "lines": 10},
           {"file": "f.py", "text": "def f():", "lines": 10},
           {"file": "g.py", "text": "def g():", "cc": 9, "lines": 10}]  # stale
v = ratchet.judge(findings, entries, ["cc", "lines"])
check("a finding with no entry is new", [f.file for f in v.new] == ["a.py"], str(v.new))
check("a ratcheted value that rose is worse — on either metric", sorted(f.file for f, _ in v.worsened) == ["b.py", "c.py"])
check("a ratcheted value that fell is improved", [f.file for f, _ in v.improved] == ["d.py"])
check("unchanged values are held, and a value the entry never recorded is not compared",
      sorted(f.file for f, _ in v.held) == ["e.py", "f.py"])
check("an entry no finding matched is stale", [e["file"] for e in v.stale] == ["g.py"])
check("the verdict fails on new or worse", v.failed and v.loose)
check("a clean verdict neither fails nor is loose",
      not ratchet.judge([findings[4]], [entries[3]], ["cc"]).failed and not ratchet.judge([findings[4]], [entries[3]], ["cc"]).loose)

# Two findings in one file sharing a declaration text — `def check(` twice — are
# told apart by occurrence order, so an unedited tree stays green (they would
# otherwise collide on the dict key and compare against each other).
dup = [F("x.py", 3, "def check():", {"cc": 9}),
       F("x.py", 30, "def check():", {"cc": 12})]
dup_entries = [f.entry() for f in dup]
vA = ratchet.judge(dup, dup_entries, ["cc"])
check("Regression A: identical same-(file,text) findings all hold, none new/stale",
      len(vA.held) == 2 and not vA.new and not vA.worsened and not vA.stale, str(vars(vA)))

worse = [F("x.py", 3, "def check():", {"cc": 9}),
         F("x.py", 30, "def check():", {"cc": 14})]  # second one grew
vB = ratchet.judge(worse, dup_entries, ["cc"])
check("Regression B: worsening one of a same-(file,text) pair worsens exactly it, the other holds",
      [f.line for f, _ in vB.worsened] == [30] and [f.line for f, _ in vB.held] == [3]
      and not vB.new and not vB.stale, str(vars(vB)))

triple = [F("y.py", 3, "def check():", {"cc": 9}),
          F("y.py", 20, "def check():", {"cc": 10}),
          F("y.py", 40, "def check():", {"cc": 11})]
triple_entries = [f.entry() for f in triple]
vC = ratchet.judge(triple, triple_entries, ["cc"])
check("Regression C: a same-(file,text) triple all holds on an identical re-run",
      len(vC.held) == 3 and not vC.new and not vC.worsened and not vC.stale, str(vars(vC)))

# --- identical (file, text): an insertion between two baselined twins is the new one (#3)
twins = [{"file": "m.py", "text": "def check(", "line": 3, "cc": 9, "lines": 10},
         {"file": "m.py", "text": "def check(", "line": 20, "cc": 12, "lines": 14}]
inserted = [F("m.py", 3, "def check(", {"cc": 9, "lines": 10}), F("m.py", 12, "def check(", {"cc": 15, "lines": 30}),
            F("m.py", 40, "def check(", {"cc": 12, "lines": 14})]
v3 = ratchet.judge(inserted, twins, ["cc", "lines"])
check("#3: a third twin inserted between two baselined ones is the new one, by line", [f.line for f in v3.new] == [12], str([f.line for f in v3.new]))
check("#3: and both neighbours are held, even though one moved 20 lines", sorted(f.line for f, _ in v3.held) == [3, 40] and not v3.worsened and not v3.stale, str(v3.worsened))
worse_and_inserted = [F("m.py", 3, "def check(", {"cc": 9, "lines": 10}), F("m.py", 12, "def check(", {"cc": 15, "lines": 30}),
                      F("m.py", 40, "def check(", {"cc": 13, "lines": 14})]
v3 = ratchet.judge(worse_and_inserted, twins, ["cc", "lines"])
check("#3: with the moved twin also worse on one value, what it still shares (its length) keeps it matched", [f.line for f in v3.new] == [12] and [f.line for f, _ in v3.worsened] == [40], str((v3.new, v3.worsened)))
legacy = [{"file": "m.py", "text": "def check(", "cc": 9, "lines": 10}, {"file": "m.py", "text": "def check(", "cc": 12, "lines": 14}]
v3 = ratchet.judge(inserted, legacy, ["cc", "lines"])
check("#3: entries written before lines were recorded still match by their values", [f.line for f in v3.new] == [12] and len(v3.held) == 2, str(v3.new))
check("the baseline entry now records the line, beside the key and the values", inserted[1].entry()["line"] == 12)

tmp = tempfile.mkdtemp(prefix="ratchet-")
try:
    legacy = os.path.join(tmp, "legacy.json")
    json.dump(entries, open(legacy, "w"))
    read_entries, prov = ratchet.read(legacy)
    check("a bare list reads as entries with no provenance", read_entries == entries and prov is None)
    fresh = os.path.join(tmp, "fresh.json")
    ratchet.write(fresh, findings[:2], ratchet.provenance("lizard", "1.0", {"cc": 8}))
    read_entries, prov = ratchet.read(fresh)
    check("write() records provenance and every value", prov["tool"] == "lizard" and read_entries[1]["cc"] == 12, str(read_entries))
    check("a missing file reads as empty", ratchet.read(os.path.join(tmp, "none.json")) == ([], None))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

p = ratchet.provenance("lizard", "1.0", {"cc": 8})
check("the config hash is stable across key order", ratchet.config_hash({"a": 1, "b": 2}) == ratchet.config_hash({"b": 2, "a": 1}))
check("same provenance: no drift", ratchet.drift_between(p, dict(p)) is None)
check("another tool drifts", "measured by swiftlint, this run by lizard" in ratchet.drift_between(dict(p, tool="swiftlint"), p))
check("another version drifts", "lizard 0.9, this run by 1.0" in ratchet.drift_between(dict(p, version="0.9"), p))
check("another config drifts", "different gate configuration" in ratchet.drift_between(ratchet.provenance("lizard", "1.0", {"cc": 7}), p))
check("an unknown version on either side is not drift", ratchet.drift_between(dict(p, version=None), p) is None)
check("no stored provenance is not drift", ratchet.drift_between(None, p) is None)

gate = ratchet.Gate("thing(s)", "over the line", "Fix it.", "tool --write-baseline",
                    show=lambda v: "cc %s" % v.get("cc"))


def report(verdict, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = ratchet.report(verdict, gate, len(entries), "OK: fine", **kw)
    return code, out.getvalue()


code, out = report(v)
check("new or worse exits 1 and prints both FAIL lines", code == 1 and "1 new thing(s) over the line, beyond the 6" in out and "2 baselined thing(s) got worse" in out, out)
check("the fix is printed; the accept command is not, even though the baseline is also loose", "Fix it." in out and "--write-baseline" not in out, out)
loose = ratchet.judge(findings[3:5], entries[2:4] + [entries[5]], ["cc"])
code, out = report(loose)
check("a loose baseline exits 0 with the notes and the tightening command", code == 0 and "improved" in out and "matched nothing" in out and "tool --write-baseline" in out, out)
code, out = report(loose, strict=True)
check("under --strict a loose baseline exits 1", code == 1 and "looser than the code" in out, out)
code, out = report(loose, quiet=True)
check("--quiet drops the OK line but not the notes", "OK: fine" not in out and "NOTE" in out, out)
clean = ratchet.judge([findings[4]], [entries[3]], ["cc"])
code, out = report(clean, quiet=True, strict=True)
check("a clean --quiet --strict run prints nothing and exits 0", code == 0 and out == "", repr(out))

suite.finish()

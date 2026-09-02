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

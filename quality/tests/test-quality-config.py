#!/usr/bin/env python3
"""The loader every check reads quality.json through.

Finding the file walks up from the working directory and stops at the root; a
missing file exits 2 naming where it looked and what to copy; a section or key
the file lacks fails naming it — never a silent default; paths resolve against
the file's directory, with `~` and absolute paths passing through.
"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import quality_config as qc

failed = 0
def check(name, ok, detail=""):
    global failed
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        failed += 1
        if detail: print("          " + str(detail)[:400])

tmp = tempfile.mkdtemp(prefix="quality-config-")
try:
    root = os.path.join(tmp, "repo"); deep = os.path.join(root, "a", "b")
    os.makedirs(deep)
    with open(os.path.join(root, "quality.json"), "w") as h:
        json.dump({"layering": {"root": "App", "home": "~/x", "abs": "/etc/hosts"}}, h)

    check("find walks up from a nested directory", qc.find(deep) == os.path.join(root, "quality.json"))
    check("find returns None above the file", qc.find(tmp) is None)

    cfg = qc.load(start=deep)
    check("root is the file's directory", cfg.root == root)
    check("a relative path resolves against the root", cfg.path("App") == os.path.join(root, "App"))
    check("~ passes through", cfg.path("~/x") == os.path.expanduser("~/x"))
    check("an absolute path passes through", cfg.path("/etc/hosts") == "/etc/hosts")
    check("paths() resolves a list", cfg.paths(["App", "/x"]) == [os.path.join(root, "App"), "/x"])
    check("get reads a key", cfg.get("layering", "root") == "App")

    try:
        cfg.section("crap"); check("a missing section raises", False)
    except KeyError as e:
        check("a missing section raises naming it", '"crap"' in str(e) and "quality.example.json" in str(e), e)
    try:
        cfg.get("layering", "ceiling"); check("a missing key raises", False)
    except KeyError as e:
        check("a missing key raises naming section and key", '"layering"' in str(e) and '"ceiling"' in str(e), e)
    check("get's default stands in for a missing key", cfg.get("layering", "ceiling", 8) == 8)

    explicit = qc.load(explicit=os.path.join(root, "quality.json"), start=tmp)
    check("--config names the file regardless of the working directory", explicit.root == root)

    # the process-level shape: no file above the working directory exits 2 with the remedy
    p = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import quality_config as q; q.load(start=sys.argv[2])",
                        os.path.join(HERE, "..", "bin"), tmp], capture_output=True, text=True)
    check("no file: exit 2", p.returncode == 2, p.stderr)
    check("no file: the message names the directory and the example to copy", tmp in p.stderr and "quality.example.json" in p.stderr, p.stderr)

    with open(os.path.join(root, "quality.json"), "w") as h: h.write("{not json")
    p = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import quality_config as q; q.load(start=sys.argv[2])",
                        os.path.join(HERE, "..", "bin"), deep], capture_output=True, text=True)
    check("unreadable file: exit 2 naming the file", p.returncode == 2 and "quality.json" in p.stderr, p.stderr)

    # this checkout: the real file parses and carries every section the checks read
    real = qc.Config(os.path.join(HERE, "..", "..", "quality.json"))
    # The language-agnostic pair every project starts with is required; the rest
    # are each project's decision (a Rust workspace has its layering in cargo, not
    # here), so their absence is noted, not failed.
    for section in ("hygiene", "doc_size"):
        check(f"this checkout's quality.json has \"{section}\"", section in real.data)
    for section in ("complexity", "crap", "layering", "reachability", "mutation"):
        check(f"this checkout's quality.json {'has' if section in real.data else 'chose not to configure'} \"{section}\"", True)

    # quality.example.json is documented (README.md) as this repository's own
    # quality.json, kept as the worked example of every section — nothing checks
    # that it stays that way, so it drifts silently unless something does.
    def first_differing_section(a, b):
        """The name of the first top-level key where a and b disagree; None if equal."""
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                return key
        return None

    example = qc.Config(os.path.join(HERE, "..", "quality.example.json"))
    if example.data.get("project") == real.data.get("project"):
        diff = first_differing_section(real.data, example.data)
        check("quality.example.json decodes equal to quality.json", diff is None,
              "section %r differs between %s and %s — the example is this repository's own config, copy it over" % (diff, real.file, example.file))
    else:
        check("quality.example.json is the template's own config (project %r), not this project's (%r) — not compared; "
              "attach.py --refresh keeps it current" % (example.data.get("project"), real.data.get("project")), True)

    equal_a = {"complexity": {"threshold": 8}, "hygiene": {"ceiling": 60}}
    equal_b = {"complexity": {"threshold": 8}, "hygiene": {"ceiling": 60}}
    unequal = {"complexity": {"threshold": 8}, "hygiene": {"ceiling": 61}}
    check("first_differing_section is None for equal dicts", first_differing_section(equal_a, equal_b) is None)
    check("first_differing_section names the section that differs in a nested value",
          first_differing_section(equal_a, unequal) == "hygiene")
finally:
    import shutil; shutil.rmtree(tmp, ignore_errors=True)

print(f"\ntest-quality-config: {'all cases passed.' if not failed else f'{failed} case(s) failed.'}")
sys.exit(1 if failed else 0)

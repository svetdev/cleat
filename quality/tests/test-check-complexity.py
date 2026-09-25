#!/usr/bin/env python3
"""test-check-complexity — assert the reader, the inline-test skip, the gate and the ratchet
in quality/bin/check-complexity.py, driven with a saved lizard CSV over a throwaway tree:
a function over the cyclomatic ceiling fails and is named, one over the length ceiling too, a
function inside a Rust `#[cfg(test)]` module is not judged, --write-baseline accepts what is over
the gate, a baselined function passes, one that grew fails, one that improved is noted and fails
under --strict, a stale entry is noted, provenance drift is noted, a missing key is named. Runs no
lizard, writes nothing outside a temporary directory.
  quality/tests/test-check-complexity.py
"""
import csv, io, json, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "check-complexity.py")
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
from extractors import complexity, swift
import ratchet

failed = 0


def check(name, ok, detail=""):
    global failed
    print("  %s  %s" % ("ok  " if ok else "FAIL", name) + ("" if ok else "\n          " + detail))
    failed += 0 if ok else 1


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)


def run(config, *args):
    proc = subprocess.run([sys.executable, SCRIPT, "--config", config, *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


tmp = tempfile.mkdtemp(prefix="check-complexity-lizard-")
try:
    src = os.path.join(tmp, "apps", "api", "src")
    knot = os.path.join(src, "knot.rs")
    write(knot, "\n".join([
        "fn simple() {}",                                    # 1
        "fn branchy(a: i32) -> i32 { if a > 0 { 1 } else { 2 } }",  # 2  (cc 9 in the CSV)
        "fn long_one() {",                                   # 3  (61 lines in the CSV)
        "}",
        "#[cfg(test)]",                                      # 5
        "mod tests {",
        "    fn test_branchy() {}",                          # 7  (cc 12 — but a test)
        "}",
        "fn after_tests() {}",                               # 9  (cc 14 — production, below the module)
        "",
    ]))
    web = os.path.join(tmp, "apps", "web", "src", "thing.ts")
    write(web, "export function tangled(a: number) { return a }\n")  # 1 (cc 10)

    csv_path = os.path.join(tmp, "lizard.csv")
    rows = [
        (1, 1, 5, 0, 1, "simple@1-1@%s" % knot, knot, "simple", "simple ( )", 1, 1),
        (1, 9, 20, 1, 1, "branchy@2-2@%s" % knot, knot, "branchy", "branchy ( a )", 2, 2),
        (2, 1, 5, 0, 61, "long_one@3-4@%s" % knot, knot, "long_one", "long_one ( )", 3, 4),
        (1, 12, 5, 0, 1, "test_branchy@7-7@%s" % knot, knot, "test_branchy", "test_branchy ( )", 7, 7),
        (1, 14, 5, 0, 1, "after_tests@9-9@%s" % knot, knot, "after_tests", "after_tests ( )", 9, 9),
        (1, 10, 8, 1, 1, "tangled@1-1@%s" % web, web, "tangled", "tangled ( a )", 1, 1),
    ]
    write(csv_path, "\n".join(",".join('"%s"' % c if isinstance(c, str) else str(c) for c in row) for row in rows) + "\n")

    functions, skipped = complexity.functions_from_csv(open(csv_path).read())
    check("the reader keys functions by realpath and start line", (os.path.realpath(knot), 2) in complexity.complexities(functions))
    check("a function inside a Rust #[cfg(test)] module is skipped, and counted as skipped",
          skipped == 1 and all(f.name != "test_branchy" for f in functions), str([f.name for f in functions]))
    check("#4: a function after the module's closing brace is production, and judged", any(f.name == "after_tests" for f in functions), str([f.name for f in functions]))
    functions_all, _ = complexity.functions_from_csv(open(csv_path).read(), skip_rust_tests=False)
    check("with skip_rust_tests off the test function is judged like any other", any(f.name == "test_branchy" for f in functions_all))

    sql = 'let q = r#"SELECT a AS "a?",\n  b AS "b?" FROM t"#;\nx?'
    masked = complexity.masked_raw_strings(sql)
    check("a raw string's body is blanked with its newlines kept, and the code around it untouched",
          masked == 'let q = r#"' + " " * len('SELECT a AS "a?",') + "\n" + " " * len('  b AS "b?" FROM t') + '"#;\nx?', repr(masked))
    check("a raw string without hashes ends at its first quote", complexity.masked_raw_strings('r"ab" + r"c"') == 'r"  " + r" "')
    check("a string that merely ends in r is not a raw string, so the code after it is kept",
          complexity.masked_raw_strings('role == "owner" && x? "no"') == 'role == "owner" && x? "no"')
    check("a byte raw string is masked like any other", complexity.masked_raw_strings('br"a?b"') == 'br"   "')
    mirror = os.path.join(tmp, "mirror")
    mirrored = complexity._mirror([src, web], mirror, ".rs", complexity.masked_raw_strings)
    copy = mirror + os.path.abspath(knot)
    check("the Rust mirror holds every .rs file at its own absolute path (as spelled, symlinks kept), and nothing else",
          os.path.exists(copy) and not os.path.exists(mirror + os.path.abspath(web)) and mirrored == [mirror + os.path.abspath(src), mirror + os.path.abspath(web)],
          str(mirrored))
    check("the mirrored file has the same line count", open(copy).read().count("\n") == open(knot).read().count("\n"))

    # --- a Rust raw string must not hide the functions after it.
    # lizard tokenises `r#"…"#` as ordinary code. In the body below the quotes and the
    # apostrophes interleave — `["']` twice — so lizard pairs the opening quote with the
    # one inside the first `["`, pairs the two apostrophes across the `"` between them,
    # and is left holding the closing quote as the *start* of a string. That string runs
    # to the next `"` anywhere in the file, swallowing `matcher`'s closing brace and
    # every function after it. A swallowed function is never reported, so it is never
    # baselined and no ceiling can ever fire on it — a whole file of them can go
    # unmeasured while the suite stays green.
    raw_lines = [
        "pub fn before(a: i32) -> i32 { if a > 0 { 1 } else { 2 } }",             # 1
        "",
        "pub fn matcher(tag: &str) -> String {",                                  # 3
        "    let pattern = format!(",
        '        r#"(<[a-zA-Z]+\\s+[^>]*?data-id=[\"\']{}[\"\'][^>]*?)>"#,',       # 5
        "        tag",
        "    );",
        "    pattern",
        "}",
        "",
        "pub fn after(a: i32) -> i32 { if a > 0 { 1 } else { 2 } }",              # 11
        "",
        'pub fn last() -> String { "done".to_string() }',                          # 13
        "",
    ]
    raw_src = os.path.join(tmp, "raw", "matcher.rs")
    write(raw_src, "\n".join(raw_lines))

    # The mask is a pure string transform, so its two load-bearing properties are asserted
    # without lizard: a line number in the copy is a line number in the original, and no
    # code outside a raw-string body is touched.
    masked_lines = complexity.masked_raw_strings("\n".join(raw_lines)).split("\n")
    check("masking keeps every line at its own length, so no reported line or column moves",
          [len(line) for line in masked_lines] == [len(line) for line in raw_lines],
          str([(i + 1, len(a), len(b)) for i, (a, b) in enumerate(zip(masked_lines, raw_lines)) if len(a) != len(b)]))
    check("the code around a masked raw string is left exactly as it was",
          [masked_lines[i] for i in (0, 2, 3, 5, 6, 7, 8, 10, 12)] == [raw_lines[i] for i in (0, 2, 3, 5, 6, 7, 8, 10, 12)],
          str(masked_lines))
    check("the raw string's body keeps neither its quotes nor its apostrophes",
          '"' not in masked_lines[4][11:-4] and "'" not in masked_lines[4][11:-4], repr(masked_lines[4]))

    # And the end-to-end claim, which needs the binary: the reader the gates call must
    # report all four, at the lines they are declared on. Without the mask lizard reports
    # two — the fixture is only a guard while it stays that hostile, so that is asserted too.
    if shutil.which("lizard"):
        measured, _ = complexity.functions_from_csv(complexity.run_lizard([raw_src], ["rust"], []), skip_rust_tests=False)
        found = {f.name: f for f in measured}
        check("every function in a file whose raw string interleaves quotes and apostrophes is measured",
              sorted(found) == ["after", "before", "last", "matcher"], str(sorted(found)))
        check("each one is reported at the line it is actually declared on",
              [(name, found[name].line) for name in ("before", "matcher", "after", "last") if name in found]
              == [("before", 1), ("matcher", 3), ("after", 11), ("last", 13)],
              str(sorted((f.line, f.name) for f in measured)))
        check("a masked raw string contributes no branches of its own",
              "matcher" in found and found["matcher"].cc == 1, str([(f.name, f.cc) for f in measured]))
        unmasked, _ = complexity.functions_from_csv(complexity._lizard([raw_src], ["rust"], []), skip_rust_tests=False)
        check("the fixture is still hostile: unmasked, lizard loses the functions after the raw string",
              sorted(f.name for f in unmasked) == ["before", "matcher"], str(sorted(f.name for f in unmasked)))
    else:
        check("lizard is not installed, so the raw-string fixture's end-to-end half is not exercised (CI installs it)", True)

    # --- lizard runs a TypeScript `function` with a plain return type past its closing brace,
    # through the interfaces after it; the reader cuts the span back to the body, and only shortens.
    typed = os.path.join(tmp, "typed", "api.ts")
    write(typed, "\n".join([
        "function filterQuery(kind?: string): string {",   # 1
        "  return kind ? `?kind=${encodeURIComponent(kind)}` : ''",
        "}",                                                            # 3
        "",
        "export interface Dashboard {",
        "  range: 'today' | '7d'",
        "  money: { total: number } | null",
        "}",
        "",
        "export function Page({ id }: { id: string }): { title: string } {",   # 10: an object return type
        "  return { title: id }",
        "}",                                                            # 12
        "",
        "export type Counts = { total: number }",
        "",
        "export const shop = {",                                        # 16
        "  read: (range: string): string => {",                         # 17: an arrow, left as lizard read it
        "    return range",
        "  },",                                                         # 19
        "}",
        "",
    ]))
    typed_rows = [(2, 2, 20, 1, 15, "filterQuery@1-15@%s" % typed, typed, "filterQuery", "filterQuery ( kind )", 1, 15),
                  (2, 1, 20, 1, 7, "Page@10-16@%s" % typed, typed, "Page", "Page ( id )", 10, 16),
                  (3, 1, 9, 1, 5, "read@17-21@%s" % typed, typed, "read", "read ( range )", 17, 21),
                  (3, 1, 9, 1, 2, "filterQuery@1-2@%s" % typed, typed, "filterQuery", "filterQuery ( kind )", 1, 2)]
    out_csv = io.StringIO()
    csv.writer(out_csv).writerows(typed_rows)
    spans = [(f.name, f.line, f.end, f.length) for f in complexity.functions_from_csv(out_csv.getvalue())[0]]
    check("a TypeScript function lizard ran through the interfaces after it is cut back to its closing brace",
          spans[0] == ("filterQuery", 1, 3, 3), str(spans))
    check("an object return type is not mistaken for the body", spans[1] == ("Page", 10, 12, 3), str(spans))
    check("an arrow function is left as lizard read it", spans[2] == ("read", 17, 21, 5), str(spans))
    check("and a span is never lengthened", spans[3] == ("filterQuery", 1, 2, 2), str(spans))
    if shutil.which("lizard"):
        proc = subprocess.run(["lizard", "--csv", "-l", "typescript", typed], capture_output=True, text=True)
        measured = {f.name: f.length for f in complexity.functions_from_csv(proc.stdout)[0]}
        check("end to end, the return-typed functions read at their real length", measured.get("filterQuery") == 3
              and measured.get("Page") == 3, str(measured) + proc.stdout)
    else:
        check("lizard is not installed, so the TypeScript span fixture's end-to-end half is not exercised (CI installs it)", True)

    # --- lizard misreads three Swift shapes: a `self.init(` call as an init declaration that
    # swallows what follows, a regex literal's brace, and an `#if` whose branches each open one.
    # Swift is read from a masked copy, line for line; a span it still runs past is cut back.
    swift_src = os.path.join(tmp, "swift", "Orchestrator.swift")
    write(swift_src, "\n".join([
        "final class Orchestrator {",
        "    init(registry: Registry, bus: Bus) {",                  # 2
        "        self.registry = registry",
        "        self.bus = bus",
        "    }",                                                      # 5
        "",
        "    convenience init(runtime: Runtime, bus: Bus) {",         # 7
        "        let registry = Registry()",
        "        registry.register(runtime)",
        "        self.init(",                                         # 10: a call, not a declaration
        "            registry: registry,",
        "            bus: bus",
        "        )",
        "    }",                                                      # 14
        "",
        "    func one(_ a: Int) -> Int { if a > 0 { return 1 }; return 0 }",   # 16
        "    func matches(_ s: String) -> Bool {",                    # 17
        "        return s.contains(/\\{[a-z]+/)",
        "    }",                                                      # 19
        "    func flagged() -> Int {",                                # 20
        "        #if DEBUG",
        "        if verbose {",
        "        #else",
        "        if quiet {",
        "        #endif",
        "            return 1",
        "        }",
        "        return 0",
        "    }",                                                      # 29
        "    func half(_ a: Int) -> Int { let s = \"a/b\"; return a / 2 / 1 }",   # 30: a string and division, untouched
        "}",
        "",
    ]))
    masked = swift.masked(open(swift_src).read()).split("\n")
    check("the Swift mask keeps every line where it was", len(masked) == 32, str(len(masked)))
    check("it renames the init a call names, and not the one a declaration does",
          "self.inix(" in masked[9] and "convenience init(" in masked[6] and "    init(registry" in masked[1], str(masked[:10]))
    check("it blanks a regex literal and every #if branch but the first",
          "{" not in masked[17] and "s.contains(" in masked[17] and "verbose" in masked[21] and masked[23].strip() == "", str(masked[17:25]))
    check("and leaves a string and a division alone", masked[29] == open(swift_src).read().split("\n")[29], masked[29])
    swift_rows = [(9, 3, 40, 0, 22, "matches@17-31@%s" % swift_src, swift_src, "matches", "matches ( s )", 17, 31),
                  (9, 3, 40, 0, 2, "one@16-17@%s" % swift_src, swift_src, "one", "one ( a )", 16, 17)]
    out_csv = io.StringIO()
    csv.writer(out_csv).writerows(swift_rows)
    spans = [(f.name, f.line, f.end) for f in complexity.functions_from_csv(out_csv.getvalue())[0]]
    check("a Swift span lizard ran to the end of its type is cut back to the body's closing brace",
          spans[0] == ("matches", 17, 19), str(spans))
    check("and a Swift span is never lengthened", spans[1] == ("one", 16, 16), str(spans))
    if shutil.which("lizard"):
        read = sorted((f.name, f.line, f.end, f.cc) for f in complexity.functions_from_csv(complexity.run_lizard([swift_src], ["swift"], []))[0])
        check("end to end, every Swift function is read at its own lines, none swallowed",
              read == [("flagged", 20, 29, 2), ("half", 30, 30, 1), ("init", 2, 5, 1), ("init", 7, 14, 1),
                       ("matches", 17, 19, 1), ("one", 16, 16, 2)], str(read))
        unmasked = complexity.functions_from_csv(complexity._lizard([swift_src], ["swift"], []), )[0]
        check("the fixture is still hostile: unmasked, lizard reads the self.init call as a function",
              any(f.name == "init" and f.line == 10 for f in unmasked), str([(f.name, f.line, f.end) for f in unmasked]))
    else:
        check("lizard is not installed, so the Swift fixture's end-to-end half is not exercised (CI installs it)", True)

    config = os.path.join(tmp, "quality.json")
    baseline = os.path.join(tmp, "complexity-baseline.json")
    write(config, json.dumps({"complexity": {"tool": "lizard",
        "sources": ["apps/api/src", "apps/web/src"], "languages": ["rust", "typescript"],
        "ceilings": {"cc": 8, "lines": 60}, "baseline": "complexity-baseline.json"}}))

    code, out = run(config, "--csv", csv_path)
    check("offenders fail the check", code == 1, out)
    check("the function over the cyclomatic ceiling is named with its numbers", "apps/api/src/knot.rs:2  cc 9, 1 lines" in out, out)
    check("the function over the length ceiling is named", "apps/api/src/knot.rs:3  cc 1, 61 lines" in out, out)
    check("the TypeScript offender is named too", "apps/web/src/thing.ts:1  cc 10" in out, out)
    check("the test-module function is not reported", "knot.rs:7" not in out, out)
    check("the simple function is not reported", "knot.rs:1" not in out, out)
    check("the baseline count is in the message", "beyond the 0 the baseline holds" in out, out)

    code, out = run(config, "--csv", csv_path, "--write-baseline")
    check("--write-baseline accepts what is over the gate", code == 0 and "4 function(s) over the gate" in out, out)
    entries, _ = ratchet.read(baseline)
    check("the baseline keys by file and declaration text",
          sorted(e["text"] for e in entries) == sorted(["fn branchy(a: i32) -> i32 { if a > 0 { 1 } else { 2 } }", "fn long_one() {", "fn after_tests() {}", "export function tangled(a: number) { return a }"]), str(entries))

    code, out = run(config, "--csv", csv_path)
    check("with everything baselined the check passes", code == 0, out)
    check("the success line carries the counts, tests skipped included", "5 functions judged (1 inline tests skipped), 4 over the gate, all 4 in the baseline" in out, out)
    code, out = run(config, "--csv", csv_path, "--quiet")
    check("--quiet prints nothing on success", out == "", repr(out))

    # --- worsened: a baselined function that grew fails; its key still matches, its numbers do not
    def write_csv(path, rows):
        write(path, "\n".join(",".join('"%s"' % c if isinstance(c, str) else str(c) for c in row) for row in rows) + "\n")
    worse = [r if r[7] != "branchy" else (1, 12) + r[2:] for r in rows]
    worse_csv = os.path.join(tmp, "worse.csv"); write_csv(worse_csv, worse)
    code, out = run(config, "--csv", worse_csv)
    check("a baselined function whose cyclomatic grew fails", code == 1, out)
    check("it is reported as worse, with both readings", "got worse" in out and "cc 12, 1 lines, was cc 9, 1 lines" in out, out)
    check("the failure output names the fix, never the accept command", "--write-baseline" not in out and "Split the function" in out, out)
    longer = [r if r[7] != "long_one" else r[:4] + (70,) + r[5:] for r in rows]
    longer_csv = os.path.join(tmp, "longer.csv"); write_csv(longer_csv, longer)
    code, out = run(config, "--csv", longer_csv)
    check("a baselined function whose length grew fails too", code == 1 and "cc 1, 70 lines, was cc 1, 61 lines" in out, out)

    # --- improved: the baseline records more than the code has — a NOTE, and a --strict failure
    run(config, "--csv", worse_csv, "--write-baseline")
    code, out = run(config, "--csv", csv_path)
    check("a baselined function that improved still passes", code == 0, out)
    check("the improvement is noted, with the tightening command",
          "improved" in out and "cc 9, 1 lines, baseline says cc 12, 1 lines" in out and "--tighten" in out, out)
    code, out = run(config, "--csv", csv_path, "--strict")
    check("--strict refuses a baseline looser than the code", code == 1 and "looser than the code" in out, out)
    run(config, "--csv", csv_path, "--write-baseline")
    code, out = run(config, "--csv", csv_path, "--strict")
    check("once tightened, --strict passes", code == 0, out)

    # --- provenance: a baseline measured by another tool, or under another config, is noted
    entries, provenance = ratchet.read(baseline)
    check("the baseline records what measured it", provenance["tool"] == "lizard" and "config" in provenance, str(provenance))
    write(baseline, json.dumps({"provenance": dict(provenance, tool="swiftlint"), "entries": entries}))
    code, out = run(config, "--csv", csv_path)
    check("a baseline measured by another tool is noted, not failed", code == 0 and "measured by swiftlint, this run by lizard" in out, out)
    code, out = run(config, "--csv", csv_path, "--strict")
    check("and under --strict it fails", code == 1, out)
    write(baseline, json.dumps({"provenance": provenance, "entries": entries}))
    write(config, json.dumps({"complexity": {"tool": "lizard",
        "sources": ["apps/api/src", "apps/web/src"], "languages": ["rust", "typescript"],
        "ceilings": {"cc": 7, "lines": 60}, "baseline": "complexity-baseline.json"}}))
    code, out = run(config, "--csv", csv_path)
    check("a baseline written under another gate configuration is noted", code == 0 and "different gate configuration" in out, out)
    write(config, json.dumps({"complexity": {"tool": "lizard",
        "sources": ["apps/api/src", "apps/web/src"], "languages": ["rust", "typescript"],
        "ceilings": {"cc": 8, "lines": 60}, "baseline": "complexity-baseline.json"}}))

    entries.append({"file": "apps/api/src/ghost.rs", "text": "fn ghost() {}", "cc": 40, "lines": 3})
    write(baseline, json.dumps(entries))
    code, out = run(config, "--csv", csv_path)
    check("a stale baseline entry does not fail the run", code == 0, out)
    check("the stale entry is named", "ghost.rs  cc 40, 3 lines  fn ghost() {}" in out, out)

    write(config, json.dumps({"complexity": {"sources": ["apps/api/src"], "languages": ["rust"], "ceilings": {"cc": 8, "lines": 60}}}))
    code, out = run(config, "--csv", csv_path)
    check("a missing key fails naming the key", code == 2 and '"baseline"' in out, out)

    # --- the legacy section name still reads as the same gate
    write(config, json.dumps({"complexity_lizard": {
        "sources": ["apps/api/src", "apps/web/src"], "languages": ["rust", "typescript"],
        "ceilings": {"cc": 8, "lines": 60}, "baseline": "complexity-baseline.json"}}))
    code, out = run(config, "--csv", csv_path, "--write-baseline")
    code, out = run(config, "--csv", csv_path)
    check("a complexity_lizard section is read as the complexity section", code == 0 and "4 over the gate" in out, out)

    # --- the retired SwiftLint-native shape is refused with the migration in the message
    write(config, json.dumps({"complexity": {"cwd": ".", "config": ".swiftlint.yml", "baseline": "b.json", "sources": ["App"]}}))
    code, out = run(config, "--csv", csv_path)
    check("the retired SwiftLint-native shape is refused naming the migration", code == 2 and "retired" in out and "--write-baseline" in out, out)

    # --- the retired shape beside a live complexity_lizard: the live one is read, the retired ignored
    write(config, json.dumps({"complexity": {"cwd": ".", "config": ".swiftlint.yml", "baseline": "b.json", "sources": ["App"]},
                              "complexity_lizard": {"sources": ["apps/api/src", "apps/web/src"], "languages": ["rust", "typescript"],
                                                    "ceilings": {"cc": 8, "lines": 60}, "baseline": "complexity-baseline.json"}}))
    code, out = run(config, "--csv", csv_path)
    check("a retired complexity beside a live complexity_lizard reads the live one", code == 0 and "4 over the gate" in out, out)
    write(config, json.dumps({"complexity": {"tool": "lizard", "sources": ["apps/api/src"], "languages": ["rust"], "ceilings": {"cc": 8, "lines": 60}, "baseline": "complexity-baseline.json"},
                              "complexity_lizard": {"sources": ["apps/web/src"], "languages": ["typescript"], "ceilings": {"cc": 8, "lines": 60}, "baseline": "other.json"}}))
    code, out = run(config, "--csv", csv_path)
    check("two live sections under both names are refused naming the pair", code == 2 and "keep one" in out, out)

    # --- SwiftLint as the reader, from a saved JSON report
    swift = os.path.join(tmp, "App", "Thing.swift")
    write(swift, "import Foundation\n\nfunc tangled(_ a: Int) -> Int { a }\n\nfunc long(_ a: Int) -> Int { a }\n\nfunc fine() {}\n")
    lint_path = os.path.join(tmp, "lint.json")
    write(lint_path, json.dumps([
        {"file": swift, "line": 3, "reason": "Function should have complexity 1 or less; currently complexity is 11"},
        {"file": swift, "line": 3, "reason": "Function body should span 1 lines or less excluding comments and whitespace: currently spans 4 lines"},
        {"file": swift, "line": 5, "reason": "Function body should span 1 lines or less excluding comments and whitespace: currently spans 70 lines"},
        {"file": swift, "line": 7, "reason": "Function should have complexity 1 or less; currently complexity is 2"}]))
    write(config, json.dumps({"complexity": {"tool": "swiftlint", "sources": ["App"], "ceilings": {"cc": 8, "lines": 60}, "baseline": "swift-baseline.json"}}))
    code, out = run(config, "--lint", lint_path)
    check("SwiftLint's complexity and length reports merge per declaration and are judged", code == 1 and "App/Thing.swift:3  cc 11, 4 lines" in out and "App/Thing.swift:5  cc 1, 70 lines" in out and "Thing.swift:7" not in out, out)
    code, out = run(config, "--lint", lint_path, "--write-baseline")
    code, out = run(config, "--lint", lint_path, "--strict")
    check("and ratchet the same way", code == 0 and "2 over the gate, all 2 in the baseline" in out, out)
    write(config, json.dumps({"complexity": {"tool": "lizard",
        "sources": ["apps/api/src", "apps/web/src"], "languages": ["rust", "typescript"],
        "ceilings": {"cc": 8, "lines": 60}, "baseline": "complexity-baseline.json"}}))

    # --- A hanging lizard is ended at a ceiling, not left to block the caller forever.
    stub_dir = os.path.join(tmp, "stub-bin")
    write(os.path.join(stub_dir, "lizard"), "#!/bin/sh\nsleep 30\n")
    os.chmod(os.path.join(stub_dir, "lizard"), 0o755)
    old_path = os.environ["PATH"]
    old_timeout = complexity.LIZARD_TIMEOUT_SECONDS
    os.environ["PATH"] = stub_dir + os.pathsep + old_path
    complexity.LIZARD_TIMEOUT_SECONDS = 1
    try:
        try:
            complexity.run_lizard(["."], ["python"], [])
            check("a hanging lizard raises LizardError", False, "run_lizard returned instead of raising")
        except complexity.ToolError as error:
            check("the error names the ceiling and that the run was ended", "1" in str(error) and "ended" in str(error), str(error))
    finally:
        os.environ["PATH"] = old_path
        complexity.LIZARD_TIMEOUT_SECONDS = old_timeout

    # --- exclude_except: a production file an exclude glob would otherwise drop by name
    # (it matches "*test-*" only because of its filename) is still judged; a file matched
    # by the exclude alone, not named in exclude_except, stays dropped.
    if shutil.which("lizard"):
        ee_src = os.path.join(tmp, "apps", "cli", "src")
        branchy_body = "\n".join(["    if a == 1: return 1", "    elif a == 2: return 2",
                                   "    elif a == 3: return 3", "    elif a == 4: return 4",
                                   "    elif a == 5: return 5", "    elif a == 6: return 6",
                                   "    elif a == 7: return 7", "    elif a == 8: return 8",
                                   "    return 0", ""])
        kept = os.path.join(ee_src, "keep-test-tool.py")
        write(kept, "def kept_fn(a):\n" + branchy_body)
        dropped = os.path.join(ee_src, "other-test-thing.py")
        write(dropped, "def dropped_fn(a):\n" + branchy_body)

        ee_config = os.path.join(tmp, "ee-quality.json")
        write(ee_config, json.dumps({"complexity": {
            "sources": ["apps/cli/src"], "languages": ["python"], "exclude": ["*test-*"],
            "exclude_except": ["apps/cli/src/keep-test-tool.py"],
            "ceilings": {"cc": 8, "lines": 60}, "baseline": "ee-baseline.json"}}))

        code, out = run(ee_config)
        check("a file matched by exclude and named in exclude_except is judged", "keep-test-tool.py" in out, out)
        check("a file matched by exclude alone is still not judged", "other-test-thing.py" not in out, out)
    else:
        check("lizard is not installed, so exclude_except is not exercised against a real run", True)

    # --- an exclude glob matches the path as the repository knows it, not the checkout's
    # absolute path: a tree under a directory literally named tmp, excluding "*/tmp/*", still
    # judges its own sources (a checkout under /tmp or .claude/worktrees used to match the
    # glob on its prefix, judge nothing and pass), while the tree's own tmp stays excluded.
    if shutil.which("lizard"):
        under_tmp = os.path.join(tmp, "tmp", "proj")
        write(os.path.join(under_tmp, "src", "deep.py"), "def deep_fn(a):\n" + branchy_body)
        write(os.path.join(under_tmp, "tmp", "scratch.py"), "def scratch_fn(a):\n" + branchy_body)
        write(os.path.join(under_tmp, "-dash", "d.py"), "def dash_fn(a):\n" + branchy_body)
        write(os.path.join(under_tmp, "real", "keep", "k.py"), "def keep_fn(a):\n" + branchy_body)
        write(os.path.join(under_tmp, "real", "generated", "g.py"), "def gen_fn(a):\n" + branchy_body)
        os.symlink(os.path.join(under_tmp, "real"), os.path.join(under_tmp, "vendor"))
        rust_body = "\n".join(["    if a == %d { return %d; }" % (i, i) for i in range(1, 10)] + ["    0", "}", ""])
        outside = os.path.join(tmp, "outside")
        write(os.path.join(outside, "lib.rs"), "fn outside_fn(a: i32) -> i32 {\n" + rust_body)
        rust_real = os.path.join(tmp, "x", "tmp", "rustreal")   # resolves under a tmp directory
        write(os.path.join(rust_real, "lib2.rs"), "fn linked_fn(a: i32) -> i32 {\n" + rust_body)
        os.symlink(rust_real, os.path.join(under_tmp, "rustlink"))
        ut_config = os.path.join(under_tmp, "quality.json")
        write(ut_config, json.dumps({"complexity": {
            "sources": ["src", "tmp", "-dash", "vendor", outside, "rustlink"], "languages": ["python", "rust"],
            "exclude": ["*/tmp/*", "tmp/*", "vendor/generated/*"],
            "ceilings": {"cc": 8, "lines": 60}, "baseline": "ut-baseline.json"}}))
        code, out = run(ut_config)
        check("a checkout under a directory named tmp still judges its sources", "deep.py" in out, out)
        check("the tree's own tmp directory is still excluded", "scratch.py" not in out, out)
        check("a root-relative source beginning with - is a path to lizard, not a flag", "d.py" in out, out)
        check("a symlinked source keeps its repository name, so its glob still excludes", "k.py" in out and "g.py" not in out, out)
        check("a Rust source outside the root is judged, not a crash", "lib.rs" in out and "Traceback" not in out, out)
        # judged, not excluded: the mirror pass sees rustlink/lib2.rs, not ../../x/tmp/rustreal/lib2.rs
        # (findings are then keyed by realpath, as every reader does)
        check("a symlinked Rust source is judged by its repository name in the mirror pass too", "lib2.rs" in out, out)
    else:
        check("lizard is not installed, so root-relative excludes are not exercised", True)
    # --- --only keeps to the configured sources: a changed file outside them is not judged.
    if shutil.which("lizard"):
        only_src = os.path.join(tmp, "apps", "svc", "src")
        inside = os.path.join(only_src, "inside.py")
        outside = os.path.join(tmp, "apps", "svc", "tests", "outside.py")
        write(inside, "def inside_fn(a):\n" + branchy_body)
        write(outside, "def outside_fn(a):\n" + branchy_body)
        only_config = os.path.join(tmp, "only-quality.json")
        write(only_config, json.dumps({"complexity": {
            "sources": ["apps/svc/src"], "languages": ["python"], "exclude": [],
            "ceilings": {"cc": 8, "lines": 60}, "baseline": "only-baseline.json"}}))
        write(os.path.join(tmp, "only-baseline.json"), json.dumps({"provenance": {}, "entries": []}))
        code, out = run(only_config, "--only", "apps/svc/tests/outside.py")
        check("--only over a changed file outside every source judges nothing", code == 0 and "outside_fn" not in out, out)
        code, out = run(only_config, "--only", "apps/svc/src/inside.py", "apps/svc/tests/outside.py")
        check("--only over a changed file inside a source still judges it", code == 1 and "inside_fn" in out, out)
        check("and still leaves the one outside alone", "outside_fn" not in out, out)
        script = os.path.join(only_src, "tool.sh")
        write(script, "tool_fn() {\n" + "\n".join("  if [ $1 = %d ]; then echo %d; fi" % (i, i) for i in range(12)) + "\n}\n")
        code, out = run(only_config, "--only", "apps/svc/src/tool.sh")
        check("--only over a changed file of a language the section does not read judges nothing",
              code == 0 and "tool_fn" not in out, out)
    else:
        check("lizard is not installed, so --only's scoping is not exercised against a real run", True)

    # --- Against this checkout, when it configures the section and lizard is installed.
    repo = os.path.dirname(os.path.dirname(HERE))
    checkout_config = os.path.join(repo, "quality.json")
    if os.path.isfile(checkout_config) and "complexity" in json.load(open(checkout_config)) and shutil.which("lizard"):
        proc = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, cwd=repo)
        check("this checkout holds its complexity baseline", proc.returncode == 0, proc.stdout + proc.stderr)
    else:
        check("this checkout configures no lizard gate (or has no lizard), so it is not judged", True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("test-check-complexity: %s" % ("all passed." if failed == 0 else "%d case(s) failed." % failed))
sys.exit(1 if failed else 0)

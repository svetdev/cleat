"""coverage — coverage reports read into one shape: LCOV and Cobertura for any
stack, plus Xcode's xccov, llvm-cov's export and istanbul's JSON.

Every stack can emit one of these two: `cargo llvm-cov --lcov`, vitest/c8's
`lcov` reporter, coverage.py's `xml` (Cobertura), JaCoCo → Cobertura, slather,
`go tool cover` through gcov2lcov. Reading them is a flag in the test command
rather than a reader per stack.

`read()` returns {absolute path: {"lines": {line: hits}, "functions":
[(start, end or None, name, hits)]}}. `function_coverage()` turns that into
the {(path, declaration line): fraction} the CRAP gate joins to complexity —
a function's coverage is the share of its executable lines that ran, its
range running to the next function's start when the report gives no end.
`line_coverage()` is the per-line view the changed-line gate reads.
"""

import glob
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET

XCCOV_TIMEOUT_SECONDS = int(os.environ.get("XCCOV_TIMEOUT_SECONDS", "600"))


class CoverageError(Exception):
    """A report could not be read; the message says why."""


def newest(pattern):
    matches = sorted(glob.glob(pattern), key=os.path.getmtime)
    return matches[-1] if matches else None




def _absolute(name, base_dir, path_map=None):
    name = remap(name, path_map)
    return os.path.realpath(name if os.path.isabs(name) else os.path.join(base_dir, name))


def remap(path, path_map):
    """`path` with the first matching `path_map` prefix replaced — coverage written
    inside a container or another checkout names `/work/apps/…` for what this
    machine calls `<repo>/apps/…`."""
    for source, target in (path_map or {}).items():
        if path.startswith(source):
            return target + path[len(source):]
    return path


def none_under(kind, named, root):
    """The CoverageError for a report that names files, none of them under `root`: read
    as it stands, every function would score 0%% — silently, which is worse than loudly."""
    return CoverageError("the %s report names %d file(s), none under %s — it was written in another checkout or "
                         "container; map its prefix to this one with \"path_map\" (%s, …)" % (kind, len(named), root, sorted(named)[0]))


def _lcov_line(entry, line):
    """Apply one LCOV record to the current file's entry."""
    if line.startswith("FN:"):
        parts = line[3:].split(",")
        entry["functions"].append([int(parts[0]), int(parts[1]) if len(parts) > 2 else None, parts[-1], 0])
    elif line.startswith("FNDA:"):
        count, name = line[5:].split(",", 1)
        entry["_hits"][name] = entry["_hits"].get(name, 0) + int(count)
    elif line.startswith("DA:"):
        number, hits = line[3:].split(",")[:2]
        entry["lines"][int(number)] = max(entry["lines"].get(int(number), 0), int(hits))


def read_lcov(text, base_dir, path_map=None):
    files = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            current = files.setdefault(_absolute(line[3:], base_dir, path_map), {"lines": {}, "functions": [], "_hits": {}})
        elif line == "end_of_record":
            current = None
        elif current is not None:
            _lcov_line(current, line)
    for entry in files.values():
        hits = entry.pop("_hits")
        entry["functions"] = [(s, e, n, hits.get(n, 0)) for s, e, n, _ in entry["functions"]]
    return files


def _note_lines(entry, elements):
    """Record <line number hits> elements into the entry; return their (number, hits)."""
    pairs = [(int(l.get("number", 0)), int(l.get("hits", 0))) for l in elements]
    for number, hits in pairs:
        entry["lines"][number] = max(entry["lines"].get(number, 0), hits)
    return pairs


def _class_entry(cls, files, sources, base_dir, path_map=None):
    name = cls.get("filename", "")
    path = _absolute(name, sources[0] if not os.path.isabs(name) else base_dir, path_map)
    entry = files.setdefault(path, {"lines": {}, "functions": []})
    _note_lines(entry, cls.findall("./lines/line"))
    for method in cls.findall("./methods/method"):
        pairs = _note_lines(entry, method.findall("./lines/line"))
        if pairs:
            numbers = [n for n, _ in pairs]
            entry["functions"].append((min(numbers), max(numbers), method.get("name", ""), sum(h for _, h in pairs)))


def read_cobertura(text, base_dir, path_map=None):
    root = ET.fromstring(text)
    sources = [s.text.strip() for s in root.iter("source") if s.text and s.text.strip()] or [base_dir]
    files = {}
    for cls in root.iter("class"):
        _class_entry(cls, files, sources, base_dir, path_map)
    return files


def read(path, base_dir, path_map=None):
    """The report at `path` — Cobertura when it is XML, LCOV otherwise."""
    with open(path, errors="replace") as handle:
        text = handle.read()
    reader = read_cobertura if text.lstrip().startswith("<") else read_lcov
    return reader(text, base_dir, path_map)


def _function_share(entry, start, end, hits):
    """The share of the executable lines in [start, end] that ran; with none, whether
    the function itself was entered."""
    inside = [n for n in entry["lines"] if start <= n <= end]
    if not inside:
        return 1.0 if hits > 0 else 0.0
    return sum(1 for n in inside if entry["lines"][n] > 0) / len(inside)


def _ranges(entry):
    """(start, end, hits) per function, an open end running to the next function's start."""
    functions = sorted(entry["functions"])
    last = max(entry["lines"] or [0])
    out = []
    for index, (start, end, _name, hits) in enumerate(functions):
        if end is None:
            end = functions[index + 1][0] - 1 if index + 1 < len(functions) else max(last, start)
        out.append((start, end, hits))
    return out


def function_coverage(report, sources_root):
    """{(abs file, declaration line): coverage in [0, 1]} for every function under
    `sources_root`."""
    root = os.path.join(os.path.realpath(sources_root), "")
    out = {}
    for path, entry in report.items():
        if not path.startswith(root):
            continue
        for start, end, hits in _ranges(entry):
            out[(path, start)] = max(out.get((path, start), 0.0), _function_share(entry, start, end, hits))
    if report and not out and not any(p.startswith(root) for p in report):
        raise none_under("coverage", set(report), sources_root)
    return out


def line_coverage(report):
    """{abs file: {line: hits}} — every executable line the report knows."""
    return {path: dict(entry["lines"]) for path, entry in report.items()}


# ---------------------------------------------------------------- Swift and web readers

def _statement_share(entry, start, end, fn_id):
    """The share of the statements in [start, end] that ran; with none, whether the
    function itself was entered."""
    statements, hits = entry.get("statementMap", {}), entry.get("s", {})
    inside = [sid for sid, st in statements.items() if start <= st.get("start", {}).get("line", -1) <= end]
    if not inside:
        return 1.0 if entry.get("f", {}).get(fn_id, 0) > 0 else 0.0
    return sum(1 for sid in inside if hits.get(sid, 0) > 0) / len(inside)


def _istanbul_function(entry, fn_id, fn):
    """(declaration line, coverage) for one fnMap entry, or None without a range."""
    loc = fn.get("loc", {})
    start, end = loc.get("start", {}).get("line"), loc.get("end", {}).get("line")
    if start is None or end is None:
        return None
    return int(fn.get("decl", {}).get("start", {}).get("line", start)), _statement_share(entry, start, end, fn_id)


class Spanned(dict):
    """{(abs file, line): coverage}, plus every function's line range, so a
    reader that knows a function only by a line inside it (lizard puts an arrow
    function on the line its parameters start, istanbul on the line its `=>`
    body does) can still ask what ran: the innermost function holding the line."""

    def __init__(self):
        super().__init__()
        self.spans = {}

    def within(self, path, line):
        spans = self.spans.get(os.path.realpath(path), ())
        holding = [(end - start, cov) for start, end, cov in spans if start <= line <= end]
        return min(holding)[1] if holding else None


def from_istanbul(report, sources_root, path_map=None):
    """{(abs file, line): coverage} from an istanbul `coverage-final.json` (vitest
    --coverage, c8/v8 or istanbul providers alike): a function's coverage is the share
    of the statements inside its range that ran; its line is its declaration's, and
    the result also answers `within(path, line)` for a line inside a function."""
    root = os.path.join(os.path.realpath(sources_root), "")
    out, named = Spanned(), set()
    for entry in report.values():
        path = os.path.realpath(remap(entry.get("path", ""), path_map))
        named.add(path)
        if not path.startswith(root):
            continue
        for fn_id, fn in entry.get("fnMap", {}).items():
            found = _istanbul_function(entry, fn_id, fn)
            if found:
                out[(path, found[0])] = max(out.get((path, found[0]), 0.0), found[1])
                loc = fn.get("loc", {})
                out.spans.setdefault(path, []).append((int(loc["start"]["line"]), int(loc["end"]["line"]), found[1]))
    if named and not any(p.startswith(root) for p in named):
        raise none_under("istanbul", named, sources_root)
    return out


def xccov_package_files(report, sources_root, path_map=None):
    """Swift files the xccov report names outside `sources_root` — a Swift package the app
    links: xccov records them in the app's bundle at 0%, so a reader keeping only the
    app root drops them, and their coverage must come from llvm-cov's export instead."""
    root = os.path.join(os.path.realpath(sources_root), "")
    out = set()
    for target in report.get("targets", []):
        for f in target.get("files", []):
            path = os.path.realpath(remap(f.get("path", ""), path_map))
            if path.endswith(".swift") and not path.startswith(root):
                out.add(path)
    return sorted(out)


def _xccov_functions(f):
    for fn in f.get("functions", []):
        lines = fn.get("executableLines", 0)
        yield int(fn.get("lineNumber", 0)), (fn.get("coveredLines", 0) / lines) if lines else 1.0


def from_xccov(report, sources_root, path_map=None):
    """{(abs file, line): coverage} from `xccov view --report --json`."""
    root = os.path.join(os.path.realpath(sources_root), "")  # under the directory — not a string prefix of it
    out, named = {}, set()
    for target in report.get("targets", []):
        for f in target.get("files", []):
            path = os.path.realpath(remap(f.get("path", ""), path_map))
            named.add(path)
            if path.startswith(root):
                out.update({(path, line): cov for line, cov in _xccov_functions(f)})
    if named and not any(p.startswith(root) for p in named):
        raise none_under("xccov", named, sources_root)
    return out


def _codecov_records(export, root, path_map):
    """(records, every file named): a record per function with a file under `root` —
    (file, mangled name, regions)."""
    records, named = [], set()
    for data in export.get("data", []):
        for fn in data.get("functions", []):
            files = [os.path.realpath(remap(p, path_map)) for p in fn.get("filenames", [])]
            named.update(files)
            files = [p for p in files if p.startswith(root)]
            regions = fn.get("regions", [])
            if files and regions:
                records.append((files[0], fn.get("name", ""), regions))
    return records, named


def _is_default_argument_thunk(name, path, names):
    """A default argument is another record, named by the body's mangled name plus
    `fA<n>_` (a closure literal as the default adds its own suffix) — not a function."""
    return any((path, name[:m.start()]) in names for m in re.finditer(r"fA\d*_", name))


def from_codecov(export, sources_root, path_map=None):
    """{(abs file, line): coverage} from llvm-cov's export (swift test --enable-code-coverage):
    a function's coverage is the share of its regions that ran; its line is its first region's.
    Default-argument thunks are skipped: a test that passes every argument never runs
    one, so a join walking down from the declaration would meet a 0% record first."""
    root = os.path.join(os.path.realpath(sources_root), "")  # under the directory, not a string prefix
    records, named_files = _codecov_records(export, root, path_map)
    if named_files and not records:
        raise none_under("llvm-cov", named_files, sources_root)
    names = {(path, name) for path, name, _ in records}
    out = {}
    for path, name, regions in records:
        if _is_default_argument_thunk(name, path, names):
            continue
        # regions: [lineStart, colStart, lineEnd, colEnd, count, fileID, expandedFileID, kind]
        ran = sum(1 for r in regions if r[4] > 0)
        key = (path, min(r[0] for r in regions))
        out[key] = max(out.get(key, 0.0), ran / len(regions))  # two records on one line: keep the best
    return out


def read_xccov_bundle(bundle):
    """`xcrun xccov view --report --json` for `bundle`, within XCCOV_TIMEOUT_SECONDS, or a CoverageError."""
    try:
        proc = subprocess.run(["xcrun", "xccov", "view", "--report", "--json", bundle],
                               capture_output=True, text=True, timeout=XCCOV_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise CoverageError("xccov ran past its %ds time limit — ended" % XCCOV_TIMEOUT_SECONDS)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise CoverageError("%s carries no coverage — the suite must run with -enableCodeCoverage YES" % bundle)
    return json.loads(proc.stdout)



def attribute_lines_above(path, line):
    """The lines directly above `line` that are attributes, nearest first."""
    try:
        with open(path, errors="replace") as handle:
            lines = handle.read().split("\n")
    except OSError:
        return []
    out = []
    current = line - 1
    while 0 < current <= len(lines) and lines[current - 1].strip().startswith("@"):
        out.append(current)
        current -= 1
    return out


def signature_lines_below(path, line):
    """The lines after `line` up to and including the one the body opens on —
    the rest of a multi-line signature — nearest first; nothing past a brace."""
    try:
        with open(path, errors="replace") as handle:
            lines = handle.read().split("\n")
    except OSError:
        return []
    # The body opens on the first `{` after the parameter list's parentheses balance —
    # a default argument may itself be a closure, `f: () -> Int = { 1 }`, and its
    # brace is inside the signature, not the end of it.
    depth = 0
    opened = False
    def opens_body(text):
        nonlocal depth, opened
        for ch in text:
            if ch == "(":
                depth += 1; opened = True
            elif ch == ")":
                depth -= 1
            elif ch == "{" and (not opened or depth == 0):
                return True
        return False
    if 0 < line <= len(lines) and opens_body(lines[line - 1]):
        return []
    out = []
    current = line + 1
    while current <= len(lines) and current <= line + 40:
        out.append(current)
        if opens_body(lines[current - 1]):
            break
        current += 1
    return out


def nearby_declaration_lines(path, line):
    """Where a coverage record for the declaration at `line` may sit instead: the
    attribute lines above it, then the rest of a multi-line signature down to the
    opening brace — nearest first, never past either into another function."""
    return attribute_lines_above(path, line) + signature_lines_below(path, line)

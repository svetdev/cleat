"""duplication — copied blocks, found here or read from a scanner's report.

A clone is a relation between locations, not a point: `Clone.locations` is
a list of (repo-relative path, first line, last line), one per copy, and
`Clone.lines` how long each copy is.

`find()` is the built-in finder, for a project with nothing installed: every
file's significant lines (whitespace collapsed; blank lines, lone braces and
punctuation-only lines dropped) are hashed in windows of `min_lines`, and a
window seen twice is the start of a clone that is extended as long as both
sides keep matching. It is exact-match after normalisation — renamed
variables are not caught, which a token-based scanner does; for that, read
its report instead. `from_jscpd()` reads jscpd's JSON report.

`density()` is the repository-wide number a ratchet holds: the share of
significant lines that sit inside some clone.
"""

import hashlib
import os

from . import patterns

PUNCTUATION_ONLY = set("{}()[];,")


class Clone:
    __slots__ = ("locations", "lines")

    def __init__(self, locations, lines):
        self.locations = locations
        self.lines = lines

    def touches(self, changed):
        """Whether any copy overlaps `changed` — {repo-relative path: {line, …}}."""
        for path, start, end in self.locations:
            lines = changed.get(path)
            if lines and any(start <= n <= end for n in lines):
                return True
        return False


def significant(text, skip_ranges=()):
    """[(line number, normalised text)] for the lines worth comparing — those inside
    `skip_ranges` (inline test modules) left out."""
    out = []
    for number, raw in enumerate(text.split("\n"), 1):
        if patterns.in_ranges(skip_ranges, number):
            continue
        line = " ".join(raw.split())
        if len(line) < 3 or set(line) <= PUNCTUATION_ONLY:
            continue
        out.append((number, line))
    return out


def significant_in(path, skip_rust_tests=True):
    """The significant lines of a file, an inline Rust test module dropped when asked."""
    with open(path, errors="replace") as handle:
        return significant(handle.read(), patterns.rust_test_ranges(path) if skip_rust_tests else ())


def _windows(lines, min_lines):
    """The hash of each `min_lines`-long window of normalised lines, by start index."""
    hashes = []
    for i in range(len(lines) - min_lines + 1):
        joined = "\n".join(text for _, text in lines[i:i + min_lines])
        hashes.append(hashlib.blake2b(joined.encode(), digest_size=12).digest())
    return hashes


def _extend(a, b, i, j, min_lines):
    """How many windows past (i, j) the two files keep matching."""
    k = 0
    while i + k + 1 < len(a) and j + k + 1 < len(b) and a[i + k + 1] == b[j + k + 1]:
        k += 1
    return k


def _continues(a, b, i, j):
    return i > 0 and j > 0 and a[i - 1] == b[j - 1]


def find(paths, repo_root, min_lines=6, skip_rust_tests=True):
    """Every clone across `paths` — pairs of copies at least `min_lines` significant
    lines long. A pair is reported at the start of the longest run, once."""
    files = []
    for path in paths:
        lines = significant_in(path, skip_rust_tests)
        files.append((os.path.relpath(path, repo_root), lines, _windows(lines, min_lines)))
    starts = {}
    for f, (_, _, hashes) in enumerate(files):
        for i, h in enumerate(hashes):
            starts.setdefault(h, []).append((f, i))
    clones = []
    for occurrences in starts.values():
        for x in range(len(occurrences)):
            for y in range(x + 1, len(occurrences)):
                clone = _clone_between(files, occurrences[x], occurrences[y], min_lines)
                if clone is not None:
                    clones.append(clone)
    clones.sort(key=lambda c: (-c.lines, c.locations))
    return clones


def _clone_between(files, first, second, min_lines):
    (fa, i), (fb, j) = first, second
    rel_a, lines_a, hashes_a = files[fa]
    rel_b, lines_b, hashes_b = files[fb]
    if fa == fb and abs(i - j) < min_lines:
        return None  # overlapping itself
    if _continues(hashes_a, hashes_b, i, j):
        return None  # reported from its start
    k = _extend(hashes_a, hashes_b, i, j, min_lines)
    length = min_lines + k
    return Clone([(rel_a, lines_a[i][0], lines_a[i + length - 1][0]),
                  (rel_b, lines_b[j][0], lines_b[j + length - 1][0])], length)


def from_jscpd(report, repo_root):
    """Clones from a jscpd JSON report (`--reporters json`)."""
    clones = []
    for dup in report.get("duplicates", []):
        locations = []
        for key in ("firstFile", "secondFile"):
            entry = dup.get(key, {})
            name = entry.get("name", "")
            rel = os.path.relpath(name, repo_root) if os.path.isabs(name) else name
            locations.append((rel, int(entry.get("start", 0)), int(entry.get("end", 0))))
        clones.append(Clone(locations, int(dup.get("lines", 0))))
    clones.sort(key=lambda c: (-c.lines, c.locations))
    return clones


def density(clones, paths, repo_root, skip_rust_tests=True):
    """(duplicated significant lines, total significant lines) across `paths`."""
    inside = {}
    for clone in clones:
        for rel, start, end in clone.locations:
            inside.setdefault(rel, set()).update(range(start, end + 1))
    total = duplicated = 0
    for path in paths:
        numbers = {n for n, _ in significant_in(path, skip_rust_tests)}
        rel = os.path.relpath(path, repo_root)
        total += len(numbers)
        duplicated += len(numbers & inside.get(rel, set()))
    return duplicated, total

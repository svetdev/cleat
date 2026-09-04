"""patterns — every site in a tree where a regex matches, per file and per line.

`files()` walks roots for the wanted suffixes, pruning skipped directory names.
`sites()` reads each file — through `prepare` first, when the caller wants
comments stripped, say — and yields (relative path, line number, line text,
pattern name) for every match of every named pattern. The line is the
site's identity for a baseline: it survives the file being edited above it.
"""

import fnmatch
import os
import re


def files(roots, suffixes, skip_dirs=(), exclude=()):
    """Every file under `roots` ending in one of `suffixes`, in walk order, skipping
    directories named in `skip_dirs` wherever they appear and files whose name or
    path matches an `exclude` glob (`*.test.ts`, `*/fixtures/*`)."""
    suffixes = tuple(suffixes)
    skip = set(skip_dirs)
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in skip)
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                if name.endswith(suffixes) and not excluded(path, exclude):
                    yield path


def excluded(path, globs):
    """Whether `path` — by its name, or as a whole — matches one of `globs`."""
    name = os.path.basename(path)
    return any(fnmatch.fnmatch(name, g) or fnmatch.fnmatch(path, g) for g in globs)


def sites(paths, patterns, repo_root, prepare=None):
    """(repo-relative path, line, line text, pattern name) for every match of every
    pattern in `patterns` ({name: regex}, `^` and `$` per line) across `paths`. `prepare(text)` transforms a
    file's text before matching — line numbers must survive it."""
    compiled = {name: re.compile(regex, re.MULTILINE) for name, regex in patterns.items()}
    for path in paths:
        with open(path, errors="replace") as handle:
            text = handle.read()
        if prepare is not None:
            text = prepare(text)
        rel = os.path.relpath(path, repo_root)
        lines = text.split("\n")
        for name, regex in compiled.items():
            for match in regex.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                yield rel, line, lines[line - 1].strip(), name


TEST_ATTRIBUTE = re.compile(r"^\s*#\[cfg\(test\)\]")
CODE_ONLY = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*')


def _ends_without_block(code):
    """A block-less item — `#[cfg(test)] use …;` — ends on the line of its `;`."""
    return ";" in code and "{" not in code and not code.strip().startswith("#[")


def _item_end(lines, start):
    """The last line of the item that begins after the attribute at `start`: the closing
    brace of its block, or the line of the `;` that ends a block-less item."""
    depth, opened = 0, False
    for number in range(start, len(lines)):
        code = CODE_ONLY.sub("", lines[number])
        if not opened and _ends_without_block(code):
            return number + 1
        depth += code.count("{") - code.count("}")
        opened = opened or "{" in code
        if opened and depth <= 0:
            return number + 1
    return len(lines)


def rust_test_ranges(path):
    """[(first line, last line)] of every `#[cfg(test)]` item in a Rust file — the inline
    test module, and nothing after its closing brace: production code appended below
    the tests is production code. Shared by every gate that reads Rust."""
    if not path.endswith(".rs"):
        return []
    try:
        with open(path, errors="replace") as handle:
            lines = handle.read().split("\n")
    except OSError:
        return []
    ranges, number = [], 0
    while number < len(lines):
        if TEST_ATTRIBUTE.match(lines[number]):
            end = _item_end(lines, number + 1)
            ranges.append((number + 1, end))
            number = end
        else:
            number += 1
    return ranges


def in_ranges(ranges, line):
    return any(start <= line <= end for start, end in ranges)

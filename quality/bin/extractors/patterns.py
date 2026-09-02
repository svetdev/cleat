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


def rust_test_boundary(path):
    """The line of the first `#[cfg(test)]` in a Rust file — an inline test module lives
    in the production file, and what is inside it is test code — or None. Shared by
    every gate that reads Rust so they agree on what is production."""
    if not path.endswith(".rs"):
        return None
    try:
        with open(path, errors="replace") as handle:
            for number, line in enumerate(handle, 1):
                if TEST_ATTRIBUTE.match(line):
                    return number
    except OSError:
        return None
    return None

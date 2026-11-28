"""mirrors — a top-level directory that is a checked-in copy of another.

Some projects commit a build of their source beside it: `deno_dist/` rewritten from
`src/` with other import paths, an `esm/` beside `lib/`. It carries no generator's
header, so every reader takes it for production code: the coverage gates ask for tests
of a copy no test runs, duplication finds every file twice, and complexity counts each
function double. The project's own commits would be refused for it.

`copies(root, suffixes, skip)` compares the top-level directories pairwise. Two are a
mirror when most of the smaller one's source files have a file at the same relative
path in the other, and most of those share nearly all their lines. Which of a mirror
is the copy is read from its name alone (`dist`, `build`, `out`, `generated`, `esm`,
`cjs`, `umd`, alone or as a word of the name): `([(copy, source)], [(a, b)])`, the
second list being mirrors whose names do not say, for a person to decide.
"""

import os
import re

BUILT_NAME = re.compile(r"(?:^|[_.-])(?:dist|build|out|gen|generated|compiled|esm|cjs|umd)(?:$|[_.-])", re.I)
MIN_SHARED = 5        # fewer files in common than this is a coincidence of names
SHARED_SHARE = 0.6    # of the smaller directory's files that the other holds at the same path
SAMPLE = 20           # shared files read to compare
SAME_LINES = 0.8      # of a file's lines found in its twin, for the two to be one file
SAME_FILES = 0.8      # of the sample that must be the same file


def _sources(root, top, suffixes, skip):
    """The source files under one top-level directory, relative to it."""
    out, base = set(), os.path.join(root, top)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in skip and not d.startswith("."))
        out.update(os.path.relpath(os.path.join(dirpath, n), base) for n in filenames if n.endswith(suffixes))
    return out


def _lines(path):
    try:
        with open(path, errors="replace") as handle:
            return {line.strip() for line in handle if line.strip()}
    except OSError:
        return set()


def _same_file(a, b):
    """Whether nearly all the lines of the smaller file are in the other."""
    one, other = sorted((_lines(a), _lines(b)), key=len)
    return bool(one) and len(one & other) / len(one) >= SAME_LINES


def _mirror(root, a, b, files):
    shared = sorted(files[a] & files[b])
    if len(shared) < MIN_SHARED or len(shared) < SHARED_SHARE * min(len(files[a]), len(files[b])):
        return False
    sample = shared[:: max(1, len(shared) // SAMPLE)][:SAMPLE]
    same = sum(_same_file(os.path.join(root, a, rel), os.path.join(root, b, rel)) for rel in sample)
    return same >= SAME_FILES * len(sample)


def _built(a, b):
    """(copy, source) when exactly one of a mirror's names says it is built, else None."""
    named = [d for d in (a, b) if BUILT_NAME.search(d)]
    return (named[0], b if named[0] == a else a) if len(named) == 1 else None


def _top_level(root, skip):
    return sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)) and d not in skip and not d.startswith("."))


def _mirrors(root, suffixes, skip):
    """Every pair of top-level directories that mirror each other."""
    tops = _top_level(root, skip)
    files = {top: _sources(root, top, tuple(suffixes), skip) for top in tops}
    return [(a, b) for i, a in enumerate(tops) for b in tops[i + 1:] if _mirror(root, a, b, files)]


def copies(root, suffixes, skip=()):
    """([(copy, source)] where the name says which is built, [(a, b)] where it does not)."""
    pairs = _mirrors(root, suffixes, skip)
    decided = list(filter(None, (_built(a, b) for a, b in pairs)))
    return decided, [pair for pair in pairs if _built(*pair) is None]

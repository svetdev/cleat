"""census — the source lines a tree holds, per file and language, read once.

`gate.py --summary` sizes each gate's debt against the code that gate reads,
and names the code no gate reads. Both come from one walk: every file whose
suffix belongs to a language in `languages.py`, with its non-blank line count.
"""

import fnmatch
import os
import re

from extractors import languages, patterns

# suffix → language, a suffix two entries share going to the first (`.js` to typescript,
# whose alias javascript is)
SUFFIX_LANGUAGE = {}
for _name, _spec in languages.LANGUAGES.items():
    for _suffix in _spec.get("suffixes", ()):
        SUFFIX_LANGUAGE.setdefault(_suffix, _name)


def language_of(name):
    """The census name of a configured language: an alias reads as what it aliases."""
    spec = languages.LANGUAGES.get(name, {})
    return spec.get("alias", name)


GENERATED_RE = re.compile(r"@generated|DO NOT EDIT|Code generated .* DO NOT EDIT|auto-?generated", re.IGNORECASE)
HEADER_LINES = 5


def _nonblank(path):
    """The file's non-blank lines; None for a file whose header says a tool wrote it."""
    try:
        with open(path, errors="replace") as handle:
            head = [next(handle, "") for _ in range(HEADER_LINES)]
            if GENERATED_RE.search("".join(head)):
                return None
            return sum(1 for line in head if line.strip()) + sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def take(root, skip_dirs=()):
    """{repo-relative path: (language, non-blank lines)} for every source file under `root`,
    hidden directories (a tool's own state: `.venv`, `.claude`, `.cache`), symlinks (the file
    is counted where it lives, once) and files whose header says a tool generated them left out."""
    found = {}
    for dirpath, filenames in patterns.walk([root], skip_dirs):
        if not _hidden(os.path.relpath(dirpath, root)):
            found.update(_counted(root, dirpath, filenames))
    return found


def _hidden(rel):
    return any(part.startswith(".") for part in rel.split(os.sep) if part != ".")


def _counted(root, dirpath, filenames):
    """{rel: (language, lines)} for the source files of one directory."""
    out = {}
    for name in filenames:
        language = SUFFIX_LANGUAGE.get(os.path.splitext(name)[1])
        path = os.path.join(dirpath, name)
        if not language or not os.path.isfile(path) or os.path.islink(path):   # a link is counted where it lives
            continue
        count = _nonblank(path)
        if count is not None:
            out[os.path.relpath(path, root)] = (language, count)
    return out


def under(rel, roots):
    return any(r in ("", ".") or rel == r or rel.startswith(r.rstrip("/") + "/") for r in roots)


def _skipped(rel, skip_dirs, exclude):
    parts = rel.split("/")[:-1]
    if any(p in skip_dirs for p in parts):
        return True
    return any(fnmatch.fnmatch(os.path.basename(rel), g) or fnmatch.fnmatch(rel, g) for g in exclude)


def read_by(census, roots, langs=None, skip_dirs=(), exclude=()):
    """The files of `census` a gate over `roots` (repo-relative) and `langs` reads."""
    wanted = {language_of(l) for l in langs} if langs else None
    roots = [os.path.normpath(r) for r in roots]
    skip = set(skip_dirs)
    return {rel for rel, (language, _) in census.items()
            if _speaks(language, wanted) and under(rel, roots) and not _skipped(rel, skip, exclude)}


def _speaks(language, wanted):
    return wanted is None or language in wanted


def lines(census, files):
    return sum(census[f][1] for f in files)


def by_language(census, files=None):
    """{language: non-blank lines} over `files` (default: all of them)."""
    out = {}
    for rel in census if files is None else files:
        language, count = census[rel]
        out[language] = out.get(language, 0) + count
    return out

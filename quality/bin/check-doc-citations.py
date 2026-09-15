#!/usr/bin/env python3
"""check-doc-citations — fail when a document cites a file that is not there.

A document that names files — an architecture note, a feature map, an
instructions file telling an agent where things live — drifts the moment a
file is renamed, and nothing in a build reads prose. This reads every
backticked path in each listed document and fails naming the ones that
resolve nowhere under the document's roots — as written, or as a bare
filename that exactly one file under the roots carries; two candidates is
ambiguity, reported with both. No parser, no baseline.

A sentence may cite a file that is not there on purpose: a backlog item that
will create it ("Add `e2e/new-flow.spec.ts` …"), a note that it is gone ("the
retired `bin/old.py`"). A citation that does not resolve is excused when its own
sentence carries a creation cue (add, create, introduce) or an absence cue
(missing, gone, removed, deleted, retired, no longer, formerly) outside
backticks; the success line counts those. A citation that resolves is judged the
same either way.

  "doc_citations": [
    {"file": "docs/architecture.md", "roots": ["src", "."]},
    {"file": "CLAUDE.md", "roots": ["."], "extensions": [".py", ".md"]}   # only paths with these suffixes are read
  ]

  quality/bin/check-doc-citations.py
  quality/bin/check-doc-citations.py --file DOC --root DIR   # one document (the tests use this)
"""

import argparse
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
from extractors import patterns

SECTION = "doc_citations"
DEFAULT_EXTENSIONS = [".py", ".ts", ".tsx", ".js", ".jsx", ".swift", ".rs", ".go", ".kt", ".java", ".rb", ".sh",
                      ".md", ".json", ".yml", ".yaml", ".toml"]
CITATION_RE = re.compile(r"`([^`\n]+?)`")
SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*_(\[])")
# Words that say a cited file is meant not to be there yet, or any more.
CUE_RE = re.compile(r"\b(?:add|adds|adding|create|creates|creating|introduce|introduces|introducing"
                    r"|missing|gone|removed|deleted|retired|no\s+longer|formerly)\b", re.IGNORECASE)


def sentences(line):
    """A line's sentences, split where one ends and a capital, a backtick or a bracket starts the next."""
    return SENTENCE_END_RE.split(line)


def cued(sentence):
    """Whether a sentence, outside its backticks, says a file is to be created or is gone."""
    return bool(CUE_RE.search(CITATION_RE.sub(" ", sentence)))


def citations(text, extensions):
    """(path, line, cued) for every backticked span that looks like a file path with one
    of `extensions` — no spaces, and a slash or a suffix — and whether its sentence
    carries a creation or absence cue."""
    out = []
    for number, line in enumerate(text.split("\n"), 1):
        for sentence in sentences(line):
            out += [(path, number, cued(sentence)) for path in paths_in(sentence, extensions)]
    return out


def paths_in(sentence, extensions):
    """The backticked spans in `sentence` that look like file paths."""
    found = []
    for span in CITATION_RE.findall(sentence):
        candidate = span.strip().split(":")[0]
        if " " in candidate or "*" in candidate or not candidate.endswith(tuple(extensions)):
            continue
        if "/" in candidate or "." in candidate:
            found.append(candidate)
    return found


def basenames_under(roots):
    """{basename: [repo paths]} for every file under the roots — a bare filename cited
    without its directory resolves through this, when it is unique. A nested checkout
    (a worktree under the tree) is not read: its copies are not this tree's files."""
    index = {}
    for root in roots:
        for dirpath, filenames in patterns.walk([root]):
            for name in filenames:
                index.setdefault(name, []).append(os.path.relpath(os.path.join(dirpath, name), root))
    return index


def resolves(path, roots, index):
    """None when `path` resolves — under a root as written, or as a bare filename found
    exactly once — else the reason it does not."""
    if any(os.path.isfile(os.path.join(root, path)) for root in roots):
        return None
    if "/" in path:
        return "not under the roots"
    found = sorted(set(index.get(path, [])))
    if len(found) == 1:
        return None
    if not found:
        return "no file of that name under the roots"
    return "ambiguous — cite one: %s" % ", ".join(found[:4])


def judge(doc_path, roots, extensions):
    with open(doc_path, errors="replace") as handle:
        cited = citations(handle.read(), extensions)
    index = basenames_under(roots)
    missing, excused = [], 0
    for path, line, is_cued in cited:
        why = resolves(path, roots, index)
        if why and is_cued:
            excused += 1
        elif why:
            missing.append((path, line, why))
    return cited, missing, excused


def entries_for(args):
    if args.file:
        return [(args.file, [os.path.abspath(r) for r in (args.root or ["."])], args.file, DEFAULT_EXTENSIONS)]
    config = quality_config.load(args.config)
    raw = config.section(SECTION)
    if not isinstance(raw, list):
        raise KeyError("%s: \"%s\" must be a list of {\"file\", \"roots\"} entries" % (config.file, SECTION))
    return [(config.path(e["file"]), config.paths(e.get("roots", ["."])), e["file"], e.get("extensions", DEFAULT_EXTENSIONS))
            for e in raw]


def main():
    parser = argparse.ArgumentParser(description="fail when a document cites a file that does not exist")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--file", help="one document to judge instead of the config's list")
    parser.add_argument("--root", action="append", help="where --file's citations may resolve (repeatable)")
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    try:
        entries = entries_for(args)
    except KeyError as problem:
        print("FAIL: %s" % problem.args[0], file=sys.stderr)
        return 2
    failed = 0
    for path, roots, shown, extensions in entries:
        if not os.path.isfile(path):
            print("FAIL: no such document: %s" % shown, file=sys.stderr)
            return 2
        failed += report(shown, roots, *judge(path, roots, extensions), quiet=args.quiet)
    return 1 if failed else 0


def report(shown, roots, cited, missing, excused, quiet):
    """Print one document's result; 1 when it failed."""
    if not missing:
        if not quiet:
            planned = " (%d named as to be created or gone)" % excused if excused else ""
            print("OK: %s — all %d cited path(s) resolve%s" % (shown, len(cited) - excused, planned))
        return 0
    print("FAIL: %s cites %d path(s) that resolve nowhere under %s:" % (shown, len(missing), ", ".join(os.path.relpath(r) for r in roots)))
    for cited_path, line, why in missing[:20]:
        print("  %s:%d  `%s` — %s" % (shown, line, cited_path, why))
    print("Point the citation at where the file is now (a bare filename resolves when exactly one file under the "
          "roots has that name), say in its sentence that the file is to be added or is gone, or delete the sentence.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

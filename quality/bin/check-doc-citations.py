#!/usr/bin/env python3
"""check-doc-citations — fail when a document cites a file that is not there.

A document that names files — an architecture note, a feature map, an
instructions file telling an agent where things live — drifts the moment a
file is renamed, and nothing in a build reads prose. This reads every
backticked path in each listed document and fails naming the ones that
resolve nowhere under the document's roots — as written, or as a bare
filename that exactly one file under the roots carries; two candidates is
ambiguity, reported with both. No parser, no baseline.

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

SECTION = "doc_citations"
DEFAULT_EXTENSIONS = [".py", ".ts", ".tsx", ".js", ".jsx", ".swift", ".rs", ".go", ".kt", ".java", ".rb", ".sh",
                      ".md", ".json", ".yml", ".yaml", ".toml"]
CITATION_RE = re.compile(r"`([^`\n]+?)`")


def citations(text, extensions):
    """(path, line) for every backticked span that looks like a file path with one of
    `extensions` — no spaces, and a slash or a suffix."""
    out = []
    for number, line in enumerate(text.split("\n"), 1):
        for span in CITATION_RE.findall(line):
            candidate = span.strip().split(":")[0]
            if " " in candidate or "*" in candidate or not candidate.endswith(tuple(extensions)):
                continue
            if "/" in candidate or "." in candidate:
                out.append((candidate, number))
    return out


SKIP_DIRS = {".git", "node_modules", "vendor", "build", ".build", "dist", "target", "__pycache__", ".venv", "venv"}


def basenames_under(roots):
    """{basename: [repo paths]} for every file under the roots — a bare filename cited
    without its directory resolves through this, when it is unique."""
    index = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
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
    missing = []
    for path, line in cited:
        why = resolves(path, roots, index)
        if why:
            missing.append((path, line, why))
    return cited, missing


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


def report(shown, roots, cited, missing, quiet):
    """Print one document's result; 1 when it failed."""
    if not missing:
        if not quiet:
            print("OK: %s — all %d cited path(s) resolve" % (shown, len(cited)))
        return 0
    print("FAIL: %s cites %d path(s) that resolve nowhere under %s:" % (shown, len(missing), ", ".join(os.path.relpath(r) for r in roots)))
    for cited_path, line, why in missing[:20]:
        print("  %s:%d  `%s` — %s" % (shown, line, cited_path, why))
    print("Point the citation at where the file is now (a bare filename resolves when exactly one file under the "
          "roots has that name), or delete the sentence that cites it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

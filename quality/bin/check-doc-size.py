#!/usr/bin/env python3
"""check-doc-size — fail if a document grows past its ceiling.

Some documents are read by an agent every run — an instructions file such as
CLAUDE.md is loaded into every agent's context, and a rule in it competes for
attention with every other sentence there: the models follow the first few
sentences and lose the fiftieth ("lost in the middle"). The instructions file
this was written for was once 6,734 words, four thousand of them the history
of each quality check, and the rules that mattered sat behind them. The
history moved into docs/ and the file came down to ~3,000; its autonomy
section then came down to what a reviewer cannot decide by machine. This keeps
it there: the ceiling is the word count on the day the document was added to
the list, rounded up a little, and a document over it fails the preflight. The
way to add a rule is to encode it as a check — the rule then costs no words —
or to cut one. Lowering a ceiling when a document shrinks is a one-line change
to the config; the success line prints both numbers. A document within
MARGIN_FRACTION of its ceiling also prints a WARN line naming the words left
— even under --quiet — so the ceiling is heard about before it is hit.

The documents and their ceilings are the "doc_size" list in quality.json:

  "doc_size": [{"file": "CLAUDE.md", "ceiling": 2300}]

  quality/bin/check-doc-size.py                 # every document in the list
  quality/bin/check-doc-size.py --quiet
  quality/bin/check-doc-size.py --config PATH
  quality/bin/check-doc-size.py --file PATH --ceiling N   # one document; the tests use this
"""

import argparse
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
from extractors import wordcount

SECTION = "doc_size"
# A document within this fraction of its ceiling is worth a WARN even on a
# passing, --quiet run — it is the one thing worth saying before the next
# small addition trips the FAIL.
MARGIN_FRACTION = 0.02


word_count = wordcount.words


def documents(args):
    """The (path, ceiling, display name) triples to judge: the one named by --file, or
    every entry of the config's list."""
    if args.file and args.ceiling is not None:
        return [(args.file, args.ceiling, args.file)]
    config = quality_config.load(args.config)
    entries = config.section(SECTION)
    if not isinstance(entries, list):
        raise KeyError(f"{config.file}: \"{SECTION}\" must be a list of {{\"file\", \"ceiling\"}} entries")

    resolve = config.path
    listed = [(resolve(entry["file"]), int(entry["ceiling"]), entry["file"]) for entry in entries]
    if not args.file:
        return listed
    wanted = os.path.abspath(args.file)
    for path, ceiling, name in listed:
        if os.path.abspath(path) == wanted:
            return [(path, ceiling, name)]
    raise KeyError(f"{config.file}: no \"{SECTION}\" entry for {args.file} — pass --ceiling N")


def main():
    parser = argparse.ArgumentParser(description="fail if a document grows past its ceiling")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--file", help="judge this one document instead of the config's list")
    parser.add_argument("--ceiling", type=int, help="the ceiling for --file (default: its entry in the config)")
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    if args.file and not os.path.isfile(args.file):
        print("FAIL: no such file: %s" % args.file, file=sys.stderr)
        return 2
    try:
        to_judge = documents(args)
    except KeyError as problem:
        print("FAIL: %s" % problem.args[0], file=sys.stderr)
        return 2
    over = 0
    for path, ceiling, name in to_judge:
        if not os.path.isfile(path):
            print("FAIL: no such file: %s" % path, file=sys.stderr)
            return 2
        words = word_count(path)
        if words > ceiling:
            over += 1
            print("FAIL: %s is %d words, over its ceiling of %d." % (name, words, ceiling))
            print("A rule that can be a check costs no words — encode it under quality/bin/ and point at it; otherwise move narrative into docs/ and keep the rule. Raising the ceiling is a decision to say why in the commit.")
            continue
        if not args.quiet:
            print("OK: %s is %d words, ceiling %d" % (name, words, ceiling))
        remaining = ceiling - words
        if remaining <= ceiling * MARGIN_FRACTION:
            print("WARN: %s is %d words, %d from its ceiling of %d." % (name, words, remaining, ceiling))
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())

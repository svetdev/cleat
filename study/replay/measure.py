#!/usr/bin/env python3
"""measure — one tree's production code by the 2026-11-03 growth measures.

  measure.py <rust|ts> <tree dir>
  measure.py <rust|ts> <clone> <sha>      (upstream's tree at a commit)

Production lines (non-blank), functions and those over cyclomatic 8 and over 15, both per
1,000 lines, the 90th-percentile and maximum cyclomatic, and lines inside a 6-line window of
significant lines that occurs more than once in the tree, per 1,000 lines. Rust inline test
modules are left out, as cleat leaves them out.
"""

import collections
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lang as languages                     # noqa: E402
import score                                 # noqa: E402  (lang put the paired runs on the path)
from extractors import duplication, patterns  # noqa: E402  (score put quality/bin on the path)

WINDOW = 6
SKIP_DIRS = {"node_modules", "target", "quality", "dist", "build", "deno_dist"}


def files(lang, root):
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        for name in names:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if lang.production(rel):
                yield rel


def measure(lang_name, root):
    lang = languages.get(lang_name)
    ccs, lines, counts, spans = [], 0, collections.Counter(), []
    for rel in files(lang, root):
        text = open(os.path.join(root, rel), errors="replace").read()
        skip = patterns.rust_test_ranges(os.path.join(root, rel)) if rel.endswith(".rs") else ()
        lines += sum(1 for n, line in enumerate(text.split("\n"), 1) if line.strip() and not patterns.in_ranges(skip, n))
        ccs += [cc for _, cc, _ in score.functions(text, rel)]
        sig = duplication.significant(text, skip)
        for i in range(len(sig) - WINDOW + 1):
            h = hashlib.blake2b("\n".join(t for _, t in sig[i:i + WINDOW]).encode(), digest_size=12).digest()
            counts[h] += 1
            spans.append((h, rel, tuple(n for n, _ in sig[i:i + WINDOW])))
    duplicated = {(rel, n) for h, rel, numbers in spans if counts[h] > 1 for n in numbers}
    kloc = lines / 1000.0 or 1.0
    ccs.sort()
    return dict(lines=lines, functions=len(ccs), over8=sum(c > 8 for c in ccs), over15=sum(c > 15 for c in ccs),
                over8_per_kloc=sum(c > 8 for c in ccs) / kloc, over15_per_kloc=sum(c > 15 for c in ccs) / kloc,
                p90=ccs[int(0.9 * (len(ccs) - 1))] if ccs else 0, max=ccs[-1] if ccs else 0,
                duplicated=len(duplicated), duplicated_per_kloc=len(duplicated) / kloc)


def upstream(lang_name, clone, sha):
    root = tempfile.mkdtemp(prefix="replay-upstream-")
    try:
        archive = subprocess.Popen(["git", "-C", clone, "archive", sha], stdout=subprocess.PIPE)
        subprocess.run(["tar", "-x", "-C", root], stdin=archive.stdout, check=True)
        archive.wait()
        return measure(lang_name, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    print(json.dumps(upstream(*args) if len(args) == 3 else measure(*args), indent=1))

#!/usr/bin/env python3
"""rreview — review.py on Rust runs: the same blind review, with Rust's production rule
deciding which files the diffs show.

  rreview.py <work dir> [jobs] [pair id …]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rrun     # noqa: E402,F401  puts Rust's production rule into mine
import review   # noqa: E402

review.PROMPT = review.PROMPT.replace("open-source Python project", "open-source Rust project")

if __name__ == "__main__":
    review.main(*sys.argv[1:])

#!/usr/bin/env python3
"""tsreview — review.py over the TypeScript runs: the same reviewer, prompt, orders and
verdicts, with production read by the TypeScript rule instead of the Python one.

  tsreview.py <work dir> [jobs] [pair id …]      (REVIEW_ARMS as for review.py)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import review    # noqa: E402
import tsdraw    # noqa: E402

review.score.mine.production = tsdraw.production
review.PROMPT = review.PROMPT.replace("open-source Python project", "open-source TypeScript project")

if __name__ == "__main__":
    review.main(*sys.argv[1:])

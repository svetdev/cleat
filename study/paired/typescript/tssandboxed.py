#!/usr/bin/env python3
"""tssandboxed — sandboxed.py for the TypeScript tasks: can the agent run the arm's gates from its own Bash?

  tssandboxed.py <clones dir> <work dir> <arm> [jobs] [task id …]

The same question and the same one cheap run a task, with tsrun's tree, allowlist and
environment in place of run.py's.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import run         # noqa: E402
import sandboxed   # noqa: E402
import tsdraw      # noqa: E402
import tsrun       # noqa: E402

_clean_env = run.clean_env


def clean_env():
    env = _clean_env()
    env.update(tsdraw.ENV_EXTRA)
    return env


run.prepare, run.ALLOWED, run.clean_env = tsrun.prepare, tsrun.ALLOWED, clean_env

if __name__ == "__main__":
    sandboxed.main(*sys.argv[1:])

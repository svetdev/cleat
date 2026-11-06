#!/usr/bin/env python3
"""tsscore — what one TypeScript agent run did, by score.py's measures where they carry over.

Resolved, cost, turns and cleat's real gates are score.py's own. The JIT study's mined
verdicts (complexity, escapes, duplication) read Python only, so they are recorded as
None and cleat's real gates are the debt measure. Functions added, the largest
cyclomatic among changed functions and lines added and deleted come from lizard over
the production TypeScript files.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import score    # noqa: E402
import draw     # noqa: E402
import mine     # noqa: E402
import tsdraw   # noqa: E402


def mined(root, start, files):
    prods = [f for f in files if tsdraw.production(f)]
    result = dict(complexity=None, escapes=None, duplication=None, max_cc_changed=0, functions_added=0,
                  la=0, ld=0, production_files=len(prods))
    for rel in prods:
        before = score.out(root, "show", "%s:%s" % (start, rel))
        path = os.path.join(root, rel)
        after = open(path, errors="replace").read() if os.path.isfile(path) else ""
        fb, fa = score.functions(before, rel), score.functions(after, rel)
        result["max_cc_changed"] = max([result["max_cc_changed"]] + [cc for _, cc, _ in score.touched(fb, fa)])
        result["functions_added"] += len(fa) - len(fb)
        result["la"] += len(mine.added_lines(before, after))
        result["ld"] += len(mine.deleted_lines(before, after))
    return result


def hidden_tests(clones, task, root):
    draw.overlay(os.path.join(clones, task["repo"]), task["sha"], root, task["overlay"])
    outcome = tsdraw.run_tests(root, task["tests"], "hidden")
    f2p = [t for t in task["f2p"] if outcome.get(t)]
    p2p = [t for t in task["p2p"] if outcome.get(t)]
    return dict(resolved=len(f2p) == len(task["f2p"]) and len(p2p) == len(task["p2p"]),
                f2p_passed=len(f2p), f2p=len(task["f2p"]), p2p_passed=len(p2p), p2p=len(task["p2p"]))


def score_run(clones, task, arm, root, out_dir):
    start = score.start_commit(root)
    files = [f for f in score.changed_files(root, start) if not f.startswith(".tsreport-")]
    result = dict(files=files, mined=mined(root, start, files), run=score.run_result(out_dir))
    events = os.path.join(root, "quality", ".events.jsonl")
    if os.path.isfile(events):
        result["cleat_events"] = [json.loads(l) for l in open(events) if l.strip()][-50:]
    result["real_gate"] = score.real_gate(clones, task, root, files, out_dir)
    result.update(hidden_tests(clones, task, root))
    return result

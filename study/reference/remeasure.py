#!/usr/bin/env python3
"""remeasure — the reference sample again, keeping what the shipped table drops:
which repository each part came from, and every production function's numbers.

  remeasure.py <repos.json> <work dir> <jobs>

Per repository: build-reference's own measurement (shallow clone of HEAD, attach,
parts), plus lizard over each part's production files: (cc, length, nloc) per
function, for the Alves/SIG thresholds. Results land in <work>/q2/<slug>.json.
"""

import concurrent.futures
import importlib.util
import json
import os
import shutil
import subprocess
import sys

CLEAT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "quality", "bin")
sys.path.insert(0, CLEAT)
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("build_reference", os.path.join(CLEAT, "build-reference.py"))
br = importlib.util.module_from_spec(spec)
spec.loader.exec_module(br)
import summary                       # noqa: E402
from extractors import languages     # noqa: E402
import lizard                        # noqa: E402

SUFFIX = {s: name for name, v in languages.LANGUAGES.items() if "suffixes" in v for s in v["suffixes"]}
LIZARD_LANGS = {"python", "typescript", "swift", "rust", "go", "kotlin", "java", "ruby"}


def functions(clone):
    out = {}
    for dirpath, dirs, files in os.walk(clone):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "vendor", "quality")]
        for name in files:
            lang = SUFFIX.get(os.path.splitext(name)[1])
            if lang not in LIZARD_LANGS:
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), clone)
            if summary._is_test(rel):
                continue
            try:
                info = lizard.analyze_file(os.path.join(dirpath, name))
            except Exception:
                continue
            out.setdefault(lang, []).extend((f.cyclomatic_complexity, f.length, f.nloc) for f in info.function_list)
    return out


def one(repo, work):
    target = os.path.join(work, "q2", br.slug(repo) + ".json")
    if os.path.isfile(target):
        return repo, "cached"
    result = br.measure_one(repo, work, keep=True)
    clone = os.path.join(work, "clones", br.slug(repo))
    try:
        if "error" not in result:
            result["functions"] = functions(clone)
    finally:
        shutil.rmtree(clone, ignore_errors=True)
    with open(target, "w") as handle:
        json.dump(result, handle)
    return repo, result.get("error", "ok")[:80]


def main(repos_path, work, jobs):
    os.makedirs(os.path.join(work, "q2"), exist_ok=True)
    with open(repos_path) as handle:
        repos = json.load(handle)
    with concurrent.futures.ProcessPoolExecutor(int(jobs)) as pool:
        for repo, status in pool.map(one, repos, [work] * len(repos)):
            print(repo, status, flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])

#!/usr/bin/env python3
"""build-reference — measure open-source repositories the way cleat measures yours.

Not a gate: it makes `quality/reference.json`, the table `gate.py --summary`
compares each part of a project with. For every repository in the list it
shallow-clones the default branch, attaches cleat (the stack detected, the
config and the day-one baselines written — exactly what an adopter gets),
splits it into parts, and records each part's production lines and its debt per
1k of them: functions over the complexity ceiling, cyclomatic over it, escape
sites, and the duplicated share. Tests are not production lines; a part is
compared only with parts in its own language.

  quality/bin/build-reference.py --repos quality/reference-repos.json --work /tmp/ref --jobs 6
  quality/bin/build-reference.py --work /tmp/ref --aggregate     # the table from what is measured

Each repository is measured in a child process with a time limit and its result
kept under `--work/results/`, so an interrupted run resumes where it stopped and
a repository that fails is recorded with the reason, not retried. The clone is
deleted after (`--keep` keeps it). The table records the repositories, lizard's
version, cleat's commit and the date, so anyone can measure it again.
"""

import argparse
import concurrent.futures
import datetime
import json
import os
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import quality_config
import reference
import summary
from extractors import census as census_
from extractors import complexity, duplication
from extractors import parts as parts_

MIN_PART_LINES = 1000      # a part smaller than this is too noisy to describe a language
TIMEOUT_SECONDS = 1800


def slug(repo):
    return repo.replace("/", "__")


def measure_one(repo, work, keep=False):
    """Clone, attach, measure; the result as a dict (with "error" when a step failed)."""
    clone = os.path.join(work, "clones", slug(repo))
    shutil.rmtree(clone, ignore_errors=True)
    try:
        _run(["git", "clone", "-q", "--depth", "1", "https://github.com/%s.git" % repo, clone], 900)
        commit = _run(["git", "-C", clone, "rev-parse", "--short", "HEAD"], 30).strip()
        _run([sys.executable, os.path.join(HERE, "attach.py"), "--into", clone], TIMEOUT_SECONDS)
        return dict(repo=repo, commit=commit, parts=measure_parts(clone))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, KeyError, ValueError) as problem:
        return {"repo": repo, "error": str(problem)[:500]}
    finally:
        if not keep:
            shutil.rmtree(clone, ignore_errors=True)


def _run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True).stdout


def measure_parts(root):
    """Every part with at least MIN_PART_LINES production lines, measured."""
    import gate
    config = quality_config.load(os.path.join(root, "quality.json"))
    baselines = gate.baselines_of(config)
    ctx = summary.Context(config, baselines, None, 90)
    found = parts_.detect(root, ctx.census, ctx.production, None, summary.CENSUS_SKIP | {"quality"})
    entries = summary.gate_entries(baselines)
    out = []
    for name, roots in found:
        files = parts_.files_of(name, roots, ctx.production, found)
        if census_.lines(ctx.census, files) < MIN_PART_LINES:
            continue
        m = parts_.metrics(ctx.census, files, parts_.debt(entries, files))
        m.update(name=name, duplication_percent=_duplication(root, files, m["language"]))
        out.append(m)
    return out


def _duplication(root, files, language):
    """The duplicated share of the part's significant lines in its main language."""
    paths = [os.path.join(root, f) for f in sorted(files) if census_.SUFFIX_LANGUAGE.get(os.path.splitext(f)[1]) == language]
    if not paths:
        return None
    dup, total = duplication.density(duplication.find(paths, root, 6), paths, root)
    return round(100.0 * dup / total, 2) if total else None


def measure_all(repos, work, jobs, keep):
    """Each repository not yet measured, in a child process apiece, `jobs` at a time."""
    results = os.path.join(work, "results")
    os.makedirs(results, exist_ok=True)
    todo = [r for r in repos if not os.path.isfile(os.path.join(results, slug(r) + ".json"))]
    print("build-reference: %d to measure, %d already measured" % (len(todo), len(repos) - len(todo)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for repo, status in zip(todo, pool.map(lambda r: _child(r, work, keep), todo)):
            print("  %-48s %s" % (repo, status), flush=True)


def _child(repo, work, keep):
    cmd = [sys.executable, os.path.abspath(__file__), "--one", repo, "--work", work] + (["--keep"] if keep else [])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS + 1000)
        result = json.loads(proc.stdout) if proc.returncode == 0 else {"repo": repo, "error": proc.stderr[-500:]}
    except (subprocess.TimeoutExpired, ValueError) as problem:
        result = {"repo": repo, "error": "timed out or unreadable: %s" % str(problem)[:200]}
    with open(os.path.join(work, "results", slug(repo) + ".json"), "w") as handle:
        json.dump(result, handle, indent=1)
    if "error" in result:
        return "error: " + result["error"].strip().splitlines()[-1][:80] if result["error"].strip() else "error"
    return "%d part(s)" % len(result["parts"])


def aggregate(work):
    """The table: per language, per metric, every measured part's value, sorted."""
    measured, failed, languages = [], [], {}
    for name in sorted(os.listdir(os.path.join(work, "results"))):
        with open(os.path.join(work, "results", name)) as handle:
            result = json.load(handle)
        if "error" in result:
            failed.append(result["repo"])
            continue
        measured.append({"repo": result["repo"], "commit": result["commit"]})
        for part in result["parts"]:
            into = languages.setdefault(part["language"], {"parts": 0})
            into["parts"] += 1
            for metric in reference.METRICS:
                if part.get(metric) is not None:
                    into.setdefault(metric, []).append(part[metric])
    for spec in languages.values():
        for metric in reference.METRICS:
            spec[metric] = sorted(spec.get(metric, []))
    provenance = {"repos": len(measured), "measured": datetime.date.today().isoformat(),
                  "lizard": complexity.lizard_version(), "cleat": _cleat_commit(), "min_part_lines": MIN_PART_LINES,
                  "sample": measured, "failed": failed}
    return {"provenance": provenance, "languages": dict(sorted(languages.items()))}


def _cleat_commit():
    """The commit the builder ran from, marked when the checkout had uncommitted changes."""
    try:
        head = _run(["git", "-C", HERE, "rev-parse", "--short", "HEAD"], 30).strip()
        dirty = _run(["git", "-C", HERE, "status", "--porcelain", "--", "."], 30).strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    return head + ("+uncommitted" if dirty else "")


def parse_args():
    parser = argparse.ArgumentParser(description="measure open-source repositories into quality/reference.json")
    parser.add_argument("--repos", help="a JSON list of owner/name repositories to measure")
    parser.add_argument("--work", required=True, help="where clones and per-repository results go")
    parser.add_argument("--jobs", type=int, default=4, help="repositories measured at once (default 4)")
    parser.add_argument("--keep", action="store_true", help="keep each clone after measuring it")
    parser.add_argument("--aggregate", action="store_true", help="write the table from the results so far")
    parser.add_argument("--out", default=reference.PATH, help="the table to write (default quality/reference.json)")
    parser.add_argument("--one", help=argparse.SUPPRESS)   # the child: measure one repository, print its result
    return parser.parse_args()


def main():
    args = parse_args()
    if args.one:
        print(json.dumps(measure_one(args.one, args.work, args.keep)))
        return 0
    if args.repos:
        with open(args.repos) as handle:
            measure_all(json.load(handle), args.work, args.jobs, args.keep)
    table = aggregate(args.work)
    with open(args.out, "w") as handle:
        json.dump(table, handle, indent=1)
        handle.write("\n")
    p = table["provenance"]
    print("build-reference: %d repositories measured, %d failed; %s written" % (p["repos"], len(p["failed"]), args.out))
    for language, spec in table["languages"].items():
        print("  %-12s %3d part(s)" % (language, spec["parts"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

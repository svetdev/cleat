"""reach — how far one part of a codebase reaches, and how wide a change is. No agent, no tokens.

  reach.py <clone> <suffixes> [pattern] [commits]

  reach.py ~/study/clones/acme__ios .swift 'import RxSwift|ApiService' 300
  reach.py ~/study/clones/acme__android .kt,.java 'ApiService' 300

For a codebase that is hard to change where the gates' numbers do not say why, or two that
are to be compared. `suffixes` are the production file endings, comma separated; test files are left out
by name. `pattern` is a regular expression for the thing suspected of holding the code in
place: an import, a base class, a service's name. Printed, at HEAD: production files and lines,
and the share of files and of lines in files whose text matches the pattern. Then, over the
last `commits` (default 300) first-parent commits that change a production file: the median and
the upper decile of production files changed, the share of commits that change a file matching
the pattern, and the share whose subject says fix, bug, regression, crash or panic, for all
commits and for those that change a matching file. Nothing printed names a file, a commit or
the pattern.
"""
import re
import statistics
import subprocess
import sys

FIX = re.compile(r"\b(fix(es|ed)?|bug|regression|crash|panic)\b", re.I)
TEST = re.compile(r"(^|/)(tests?|androidTest|[A-Za-z]*Tests|UITests|mocks?|fixtures?)/|Tests?\.[a-z]+$|Spec\.[a-z]+$", re.I)


def git(clone, *args):
    done = subprocess.run(["git", "-C", clone, *args], capture_output=True, text=True, errors="replace", check=True)
    return done.stdout


def production(path, suffixes):
    return path.endswith(suffixes) and not TEST.search(path)


def matching_files(clone, pattern):
    """The paths at HEAD whose text matches the pattern."""
    if not pattern:
        return set()
    found = subprocess.run(["git", "-C", clone, "grep", "-l", "-E", pattern, "HEAD"], capture_output=True, text=True)
    return {line.split(":", 1)[1] for line in found.stdout.splitlines() if ":" in line}


def lines_at_head(clone, paths):
    """Non-blank lines per path at HEAD."""
    counts = {}
    for path in paths:
        text = git(clone, "show", f"HEAD:{path}")
        counts[path] = sum(1 for line in text.splitlines() if line.strip())
    return counts


def commits(clone, suffixes, most):
    """(subject, production files changed) for the newest first-parent commits that change one."""
    log = git(clone, "log", "--first-parent", "--no-merges", "-n", str(most * 4), "--format=\x01%s", "--name-only")
    rows = []
    for block in log.split("\x01")[1:]:
        subject, _, names = block.partition("\n")
        changed = [name for name in names.split("\n") if production(name, suffixes)]
        if changed:
            rows.append((subject, changed))
    return rows[:most]


def share(part, whole):
    return f"{100 * part / whole:.0f}% ({part} of {whole})" if whole else "none"


def head_report(clone, suffixes, matched):
    files = [path for path in git(clone, "ls-tree", "-r", "--name-only", "HEAD").splitlines() if production(path, suffixes)]
    lines = lines_at_head(clone, files)
    total = sum(lines.values())
    held = [path for path in files if path in matched]
    print(f"production files {len(files)}, lines {total}")
    print(f"files matching the pattern  {share(len(held), len(files))}")
    print(f"lines in matching files     {share(sum(lines[path] for path in held), total)}")


def history_report(rows, matched):
    sizes = sorted(len(changed) for _, changed in rows)
    touching = [row for row in rows if any(path in matched for path in row[1])]
    print(f"commits read {len(rows)}")
    print(f"production files a commit changes: median {statistics.median(sizes)}, upper decile {sizes[int(len(sizes) * 0.9)]}")
    print(f"commits changing a matching file    {share(len(touching), len(rows))}")
    print(f"fix commits, all                    {share(sum(1 for s, _ in rows if FIX.search(s)), len(rows))}")
    print(f"fix commits, among those matching   {share(sum(1 for s, _ in touching if FIX.search(s)), len(touching))}")


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    clone, suffixes = sys.argv[1], tuple(sys.argv[2].split(","))
    pattern = sys.argv[3] if len(sys.argv) > 3 else ""
    most = int(sys.argv[4]) if len(sys.argv) > 4 else 300
    matched = matching_files(clone, pattern)
    head_report(clone, suffixes, matched)
    rows = commits(clone, suffixes, most)
    if rows:
        history_report(rows, matched)


if __name__ == "__main__":
    main()

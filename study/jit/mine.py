#!/usr/bin/env python3
"""mine — one repository's history as change-level rows for the JIT study.

Per non-merge commit touching production Python (cleat's own test/aside rule):
size controls (la, ld, nf, entropy, exp), whether it is a bug fix (the
pre-registered message rule), what cleat's gates would say about it
(complexity, escapes, duplication — cleat's own extractors and attach defaults),
and whether B-SZZ marks it bug-inducing.

  mine.py <bare repo> <out.csv>
"""

import ast
import collections
import csv
import difflib
import io
import math
import os
import re
import subprocess
import sys

CLEAT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "quality", "bin")
sys.path.insert(0, CLEAT)
sys.dont_write_bytecode = True
import summary                                  # noqa: E402  cleat's test/aside rule
from extractors import duplication, languages   # noqa: E402
import lizard                                   # noqa: E402

CC, LINES, DUP = 8, 60, 6
MAX_FILES = 100
FIX_RE = re.compile(r"\b(fix(e[sd]|ing)?|bugs?|defects?|regressions?|crash(e[sd]|ing)?)\b", re.I)
NOT_FIX_RE = re.compile(r"\b(typos?|docs?|documentation|readme|lint(ing)?|style|format(ting)?|refactor(s|ed|ing)?)\b", re.I)
ESCAPES = [re.compile(p, re.M) for p in languages.LANGUAGES["python"]["patterns"].values()]
NULL = "0" * 40


def is_fix(message):
    return bool(FIX_RE.search(message)) and not NOT_FIX_RE.search(message)


def production(path):
    return path.endswith(".py") and not summary._is_test(path)


class Git:
    def __init__(self, repo):
        self.repo = repo
        self.cat = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def run(self, *args):
        return subprocess.run(["git", "-C", self.repo, *args], capture_output=True,
                              check=True).stdout.decode("utf-8", "replace")

    def blob(self, sha):
        if sha == NULL:
            return ""
        self.cat.stdin.write(sha.encode() + b"\n")
        self.cat.stdin.flush()
        header = self.cat.stdout.readline().split()
        if len(header) < 3 or header[1] != b"blob":
            return ""
        data = self.cat.stdout.read(int(header[2]))
        self.cat.stdout.read(1)
        return data.decode("utf-8", "replace")


def commits(git):
    """Non-merge commits, parents first, with their raw and numstat file lists."""
    out = git.run("log", "--no-merges", "--date-order", "--reverse", "-M", "--no-abbrev",
                  "--raw", "--numstat", "--format=%x00%H%x01%P%x01%ae%x01%ct%x01%B%x02", "HEAD")
    for chunk in out.split("\x00")[1:]:
        head, _, files = chunk.partition("\x02")
        sha, parents, author, ct, message = head.split("\x01", 4)
        raw, numstat = [], {}
        for line in files.strip("\n").split("\n"):
            if line.startswith(":"):
                meta, *paths = line.split("\t")
                _, _, old, new, status = meta[1:].split()
                raw.append((old, new, status[0], paths[0], paths[-1]))
            elif line:
                parts = line.split("\t")
                if len(parts) >= 3:
                    a, d = parts[0], parts[1]
                    path = parts[-1]
                    if "=>" in path:          # rename in numstat: keep the new name
                        path = re.sub(r"\{[^{}]*=> ([^{}]*)\}", r"\1", path).replace("//", "/")
                        path = path.split(" => ")[-1]
                    numstat[path] = (0 if a == "-" else int(a), 0 if d == "-" else int(d))
        yield dict(sha=sha, parent=(parents.split() or [None])[0], author=author, ct=int(ct),
                   message=message, raw=raw, numstat=numstat)


class Cache:
    def __init__(self, git):
        self.git = git
        self.funcs, self.windows, self.text = {}, {}, collections.OrderedDict()

    def content(self, sha):
        if sha in self.text:
            self.text.move_to_end(sha)
            return self.text[sha]
        value = self.git.blob(sha)
        self.text[sha] = value
        if len(self.text) > 4000:
            self.text.popitem(last=False)
        return value

    def functions(self, sha, path):
        if sha not in self.funcs:
            try:
                info = lizard.analyze_file.analyze_source_code(path, self.content(sha))
                self.funcs[sha] = [(f.name, f.cyclomatic_complexity, f.length) for f in info.function_list]
            except Exception:
                self.funcs[sha] = []
        return self.funcs[sha]

    def wins(self, sha):
        """[(hash, (significant line numbers))] of each DUP-line window, cleat's normalisation."""
        if sha not in self.windows:
            lines = duplication.significant(self.content(sha))
            out = []
            for i in range(len(lines) - DUP + 1):
                joined = "\n".join(t for _, t in lines[i:i + DUP])
                out.append((hash(joined), tuple(n for n, _ in lines[i:i + DUP])))
            self.windows[sha] = out
        return self.windows[sha]


def complexity_fails(before, after):
    """A function over a ceiling that is new or worse than its namesake (cleat's ratchet pairing)."""
    old = collections.defaultdict(list)
    for name, cc, length in before:
        old[name].append((cc, length))
    for name, cc, length in after:
        match = old[name].pop(0) if old[name] else None   # namesakes pair in order (lizard drops the class)
        if cc <= CC and length <= LINES:
            continue
        if match is None or cc > match[0] or length > match[1]:
            return True
    return False


def escape_counts(text):
    return [len(p.findall(text)) for p in ESCAPES]


def added_lines(before, after):
    a, b = before.split("\n"), after.split("\n")
    out = set()
    for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "insert"):
            out.update(range(j1 + 1, j2 + 1))
    return out


def deleted_lines(before, after):
    """Line numbers in `before` that the change removed, whitespace-only edits ignored (-w)."""
    a = [" ".join(x.split()) for x in before.split("\n")]
    b = [" ".join(x.split()) for x in after.split("\n")]
    out = []
    for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            out.extend(n + 1 for n in range(i1, i2) if a[n] and not a[n].startswith("#"))
    return out


def entropy(sizes):
    total = sum(sizes)
    if total == 0 or len(sizes) < 2:
        return 0.0
    h = -sum(s / total * math.log2(s / total) for s in sizes if s)
    return h / math.log2(len(sizes))


class Tree:
    """Production-Python window index of the tree at `self.at`, moved commit to commit."""

    def __init__(self, git, cache):
        self.git, self.cache = git, cache
        self.at, self.blobs, self.index = None, {}, collections.Counter()

    def move(self, sha):
        if self.at is None:
            listing = self.git.run("ls-tree", "-r", "--full-tree", sha)
            changes = []
            for line in listing.splitlines():
                meta, path = line.split("\t", 1)
                parts = meta.split()
                if parts[1] == "blob" and production(path):
                    changes.append((path, parts[2]))
        else:
            diff = self.git.run("diff-tree", "-r", "--no-abbrev", "--no-renames", self.at, sha)
            changes = []
            for line in diff.splitlines():
                if line.startswith(":"):
                    meta, path = line.split("\t", 1)
                    if production(path):
                        changes.append((path, meta.split()[3]))
        for path, new in changes:
            old = self.blobs.pop(path, None)
            if old:
                self.index.subtract(h for h, _ in self.cache.wins(old))
            if new != NULL:
                self.blobs[path] = new
                self.index.update(h for h, _ in self.cache.wins(new))
        self.at = sha

    def copied(self, path, blob, added):
        """Whether a DUP-line window covering an added line appears elsewhere in the tree."""
        wins = self.cache.wins(blob)
        positions = collections.defaultdict(list)
        for i, (h, _) in enumerate(wins):
            positions[h].append(i)
        for i, (h, numbers) in enumerate(wins):
            if not added.intersection(numbers):
                continue
            if self.index[h] > len(positions[h]):
                return True
            if any(abs(i - j) >= DUP for j in positions[h]):
                return True
        return False


def pure_reformat(git, cache, commit):
    """Every changed production file parses to the same AST before and after."""
    files = [r for r in commit["raw"] if production(r[4]) and r[2] in "MR"]
    if not files:
        return False
    for old, new, _, _, _ in files:
        try:
            if ast.dump(ast.parse(cache.content(old))) != ast.dump(ast.parse(cache.content(new))):
                return False
        except (SyntaxError, ValueError, RecursionError):
            return False
    return True


def blame(git, parent, path, numbers):
    """{line: commit} for `numbers` of `path` at `parent`."""
    out = {}
    numbers = sorted(set(numbers))
    for k in range(0, len(numbers), 200):
        args = ["blame", "-w", "-C", "--porcelain"]
        for n in numbers[k:k + 200]:
            args += ["-L", "%d,%d" % (n, n)]
        try:
            text = git.run(*args, parent, "--", path)
        except subprocess.CalledProcessError:
            continue
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 3 and len(parts[0]) == 40 and parts[1].isdigit():
                out[int(parts[1])] = parts[0]
    return out


def main(repo, out_path):
    git = Git(repo)
    cache = Cache(git)
    tree = Tree(git, cache)
    rows, by_sha, authors = [], {}, collections.Counter()
    fixes = []
    for c in commits(git):
        exp = authors[c["author"]]
        authors[c["author"]] += 1
        files = [r for r in c["raw"] if production(r[4])]
        if not files or len(c["raw"]) > MAX_FILES:
            by_sha[c["sha"]] = dict(sha=c["sha"], big=len(c["raw"]) > MAX_FILES, raw=c["raw"], modelled=False)
            continue
        sizes = [sum(c["numstat"].get(r[4], (0, 0))) for r in files]
        la = sum(c["numstat"].get(r[4], (0, 0))[0] for r in files)
        ld = sum(c["numstat"].get(r[4], (0, 0))[1] for r in files)
        tree.move(c["sha"])
        cx = esc = dup = False
        for old, new, status, _, path in files:
            if new == NULL:
                continue
            before, after = cache.content(old), cache.content(new)
            if not cx and complexity_fails(cache.functions(old, path) if old != NULL else [],
                                           cache.functions(new, path)):
                cx = True
            if not esc and any(b > a for a, b in zip(escape_counts(before), escape_counts(after))):
                esc = True
            if not dup and tree.copied(path, new, added_lines(before, after)):
                dup = True
        fix = is_fix(c["message"])
        row = dict(sha=c["sha"], ct=c["ct"], la=la, ld=ld, nf=len(files), entropy=round(entropy(sizes), 4),
                   exp=exp, fix=int(fix), complexity=int(cx), escapes=int(esc), duplication=int(dup), buggy=0)
        rows.append(row)
        by_sha[c["sha"]] = dict(sha=c["sha"], big=False, raw=c["raw"], modelled=True, row=row)
        if fix and c["parent"]:
            fixes.append(c)
    # B-SZZ: blame the deleted lines of each fix at its parent
    reformat = {}
    for c in fixes:
        for old, new, status, oldpath, path in c["raw"]:
            if status not in "MR" or not production(path):
                continue
            gone = deleted_lines(cache.content(old), cache.content(new))
            if not gone:
                continue
            for _, inducer in blame(git, c["parent"], oldpath, gone).items():
                info = by_sha.get(inducer)
                if not info or not info["modelled"] or info["big"]:
                    continue
                if inducer not in reformat:
                    reformat[inducer] = pure_reformat(git, cache, info)
                if not reformat[inducer]:
                    info["row"]["buggy"] = 1
    with open(out_path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["sha"])
        writer.writeheader()
        writer.writerows(rows)
    print("%s commits=%d fixes=%d buggy=%d" % (os.path.basename(repo), len(rows), len(fixes),
                                                 sum(r["buggy"] for r in rows)), flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

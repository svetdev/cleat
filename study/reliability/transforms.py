#!/usr/bin/env python3
"""transforms — the meaning-preserving variants of data/orig: rename, reorder, unparse, black.

rename   consistent renaming, by AST position, of (a) every function's non-parameter locals
         (skipping a function that calls locals()/vars()/eval/exec, uses an f-string `=`
         specifier, or a name some nested scope rebinds, declares global/nonlocal, or binds by
         except/import/match), and (b) private (single-underscore) top-level functions and
         methods whose name appears as a word in no other .py file of the clone at HEAD
         (`git grep -w`, read-only) and in this file only as a def, a Name or an attribute.
         Formatting and comments are untouched: only identifier bytes change.
reorder  within each maximal run of consecutive top-level def/class statements, the movable
         ones reversed: a def is movable when it reads none of the run's names at definition
         time (decorators, defaults, annotations, bases, class bodies), no other def of the run
         reads its name at definition time, and its name is defined once in the run.
unparse  ast.unparse(ast.parse(text)).
black    black (default settings) over a copy of orig.
Writes data/<variant>/<clone>/…, and data/<variant>.keymap.json {clone: {rel: {new key: orig key}}}.
"""
import ast, io, json, os, re, shutil, subprocess, sys, tokenize, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, S, clones, py_files, def_index

CLONES = os.path.join(S, "work/clones")
SUFFIX = "_q3r"
FSTRING_EQ = re.compile(r"[^=!<>]=\s*(![rsa])?\s*(:[^{}]*)?\}")
NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
          ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def own_scope(fn):
    """Nodes in fn's own scope (not descending into nested scopes)."""
    stack = list(fn.body)
    while stack:
        n = stack.pop()
        yield n
        for c in ast.iter_child_nodes(n):
            if not isinstance(c, NESTED):
                stack.append(c)
            else:
                yield c  # the nested node itself (its decorators/defaults are evaluated here, close enough)


def bound_in_nested(fn):
    names = set()
    for n in ast.walk(fn):
        if n is fn:
            continue
        if isinstance(n, NESTED):
            for m in ast.walk(n):
                if isinstance(m, ast.Name) and isinstance(m.ctx, (ast.Store, ast.Del)):
                    names.add(m.id)
                elif isinstance(m, ast.arg):
                    names.add(m.arg)
                elif isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and m is not n:
                    names.add(m.name)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(n.name)
            if isinstance(n, ast.ClassDef):
                for m in ast.walk(n):
                    if isinstance(m, ast.Name):
                        names.add(m.id)   # class scopes resolve names differently: leave them alone
    return names


def local_renames(fn, src_seg):
    """The non-parameter locals of fn that can be renamed consistently."""
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("locals", "vars", "eval", "exec", "dir"):
            return set()
        if isinstance(n, ast.JoinedStr) and src_seg is not None:
            seg = ast.get_source_segment(src_seg, n) or ""
            if FSTRING_EQ.search(seg):
                return set()
    params = {a.arg for a in ast.walk(fn.args) if isinstance(a, ast.arg)}
    blocked = set(params) | bound_in_nested(fn)
    stores = set()
    for n in own_scope(fn):
        if isinstance(n, (ast.Global, ast.Nonlocal)):
            blocked.update(n.names)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            blocked.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            blocked.update((a.asname or a.name).split(".")[0] for a in n.names)
        elif isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
            blocked.add(n.name)
        elif isinstance(n, ast.MatchMapping) and n.rest:
            blocked.add(n.rest)
        elif isinstance(n, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple)):
            blocked.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            stores.add(n.id)
    for n in ast.walk(fn):  # global/nonlocal anywhere below
        if isinstance(n, (ast.Global, ast.Nonlocal)):
            blocked.update(n.names)
    return {s for s in stores if s not in blocked and not s.startswith("__")}


def byte_edit(lines_b, edits):
    """Apply (line, col_start, col_end, new) byte edits, right to left per line."""
    per = collections.defaultdict(list)
    for e in set(edits):
        per[e[0]].append(e)
    for ln, es in per.items():
        b = lines_b[ln - 1]
        for _, s, e, new in sorted(es, key=lambda x: -x[1]):
            b = b[:s] + new.encode() + b[e:]
        lines_b[ln - 1] = b
    return lines_b


def def_name_pos(lines_b, node):
    """(line, col_start, col_end) of a def's name token."""
    ln = node.lineno
    line = lines_b[ln - 1]
    m = re.search(rb"\bdef\s+(" + re.escape(node.name.encode()) + rb")\b", line)
    if not m:
        return None
    return (ln, m.start(1), m.end(1))


def grep_elsewhere(bare, name, rel):
    """Whether `name` appears as a word in a .py file of the clone other than rel (git grep at HEAD)."""
    p = subprocess.run(["git", "-C", bare, "grep", "-l", "-w", "-F", name, "HEAD", "--", "*.py", "*.pyi"],
                       capture_output=True, text=True)
    files = {l.split(":", 1)[1] for l in p.stdout.split("\n") if ":" in l}
    return bool(files - {rel})


def private_function_renames(tree, text, lines_b, bare, rel):
    """Edits renaming eligible private functions/methods, and {old: new}."""
    defs = collections.defaultdict(list)
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs[("top", n.name)].append(n)
        if isinstance(n, ast.ClassDef):
            for m in n.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defs[("method", m.name)].append(m)
    names = {}
    for (kind, name), nodes in defs.items():
        if not (name.startswith("_") and not name.startswith("__")) or name in names:
            continue
        names[name] = kind
    # tokens: every NAME occurrence must be accounted for; no string may contain the word
    toks = list(tokenize.tokenize(io.BytesIO(text.encode()).readline))
    edits, renamed = [], {}
    all_defs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    for name, kind in names.items():
        if re.search(r"\b%s\b" % re.escape(name), " ".join(t.string for t in toks if t.type in (tokenize.STRING, tokenize.FSTRING_MIDDLE))):
            continue
        new = name + SUFFIX
        positions = set()
        ok = True
        for d in all_defs:
            if d.name == name:
                if kind == "top" and d not in tree.body:
                    ok = False  # a nested def of the same name: leave the file alone
                p = def_name_pos(lines_b, d) if not isinstance(d, ast.ClassDef) else None
                if p is None:
                    ok = False
                    break
                positions.add(p)
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and n.id == name:
                if kind == "method":
                    ok = False
                positions.add((n.lineno, n.col_offset, n.end_col_offset))
            elif isinstance(n, ast.Attribute) and n.attr == name:
                if kind == "top":
                    ok = False
                if n.end_lineno is None:
                    ok = False
                    continue
                end = n.end_col_offset
                positions.add((n.end_lineno, end - len(name.encode()), end))
            elif isinstance(n, ast.arg) and n.arg == name or isinstance(n, ast.keyword) and n.arg == name:
                ok = False
            elif isinstance(n, (ast.ImportFrom, ast.Import)) and any((a.asname or a.name) == name or a.name == name for a in n.names):
                ok = False
        name_toks = [t for t in toks if t.type == tokenize.NAME and t.string == name]
        if not ok or len(name_toks) != len(positions):
            continue
        # tokenize columns are characters; verify by decoding
        if grep_elsewhere(bare, name, rel):
            continue
        for ln, s, e in positions:
            if lines_b[ln - 1][s:e] != name.encode():
                ok = False
        if not ok:
            continue
        edits += [(ln, s, e, new) for ln, s, e in positions]
        renamed[name] = new
    return edits, renamed


def rename_file(text, bare, rel):
    tree = ast.parse(text)
    lines_b = text.encode().split(b"\n")
    edits = []
    n_locals = 0
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            targets = {t for t in local_renames(fn, text) if (t + SUFFIX) not in text}
            n_locals += len(targets)
            for n in ast.walk(fn):
                if isinstance(n, ast.Name) and n.id in targets:
                    edits.append((n.lineno, n.col_offset, n.end_col_offset, n.id + SUFFIX))
    fedits, renamed = private_function_renames(tree, text, lines_b, bare, rel)
    lines_b = byte_edit(lines_b, edits + fedits)
    out = b"\n".join(lines_b).decode()
    ast.parse(out)  # still valid
    return out, n_locals, renamed


def eager_names(node):
    """Names read when a top-level def/class statement executes."""
    parts = list(node.decorator_list)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = node.args
        parts += a.defaults + [d for d in a.kw_defaults if d]
        parts += [x.annotation for x in a.args + a.posonlyargs + a.kwonlyargs + [a.vararg, a.kwarg] if x is not None and x.annotation]
        if node.returns:
            parts.append(node.returns)
    else:
        parts += node.bases + [k.value for k in node.keywords]
        stack = list(node.body)
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parts += n.decorator_list + n.args.defaults
                continue
            parts.append(n)
    return {m.id for p in parts for m in ast.walk(p) if isinstance(m, ast.Name)}


def reorder_file(text):
    tree = ast.parse(text)
    lines = text.split("\n")
    body = tree.body
    runs, cur = [], []
    for n in body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            cur.append(n)
        else:
            if len(cur) > 1:
                runs.append(cur)
            cur = []
    if len(cur) > 1:
        runs.append(cur)
    moved = 0
    replacements = []
    for run in runs:
        names = collections.Counter(n.name for n in run)
        dup = {k for k, v in names.items() if v > 1}
        eager = {id(n): eager_names(n) for n in run}
        needed = set().union(*eager.values()) & set(names)   # run names some def reads when it executes
        movable = [n for n in run if n.name not in dup and n.name not in needed and not (eager[id(n)] & set(names))]
        if len(movable) < 2:
            continue
        # reverse the movable ones among themselves, others keep their slots
        order = list(run)
        slots = [i for i, n in enumerate(run) if n in movable]
        rev = list(reversed(movable))
        for i, n in zip(slots, rev):
            order[i] = n
        def span(n):
            start = min([d.lineno for d in n.decorator_list] + [n.lineno])
            # carry comment lines directly above
            while start > 1 and lines[start - 2].lstrip().startswith("#"):
                start -= 1
            return start, n.end_lineno
        spans = [span(n) for n in run]
        first, last = spans[0][0], spans[-1][1]
        # the text between consecutive defs (blank lines) is kept in its slot
        chunks = {id(n): "\n".join(lines[s - 1:e]) for n, (s, e) in zip(run, spans)}
        gaps = ["\n".join(lines[spans[i][1]:spans[i + 1][0] - 1]) for i in range(len(run) - 1)]
        pieces = []
        for i, n in enumerate(order):
            pieces.append(chunks[id(n)])
            if i < len(gaps):
                pieces.append(gaps[i])
        new = "\n".join(p for p in pieces)
        replacements.append((first, last, new))
        moved += sum(1 for a, b in zip(run, order) if a is not b)
    for first, last, new in sorted(replacements, reverse=True):
        lines[first - 1:last] = new.split("\n")
    out = "\n".join(lines)
    ast.parse(out)
    return out, moved


def keys_of(text):
    return [k for _, _, k in def_index(text)]


def main(which):
    stats = {}
    for variant in which:
        dst_root = os.path.join(DATA, variant)
        if os.path.exists(dst_root):
            shutil.rmtree(dst_root)
        keymap, st = {}, collections.Counter()
        if variant == "black":
            shutil.copytree(os.path.join(DATA, "orig"), dst_root)
            p = subprocess.run([os.path.join(S, "venv/bin/black"), "-q", dst_root], capture_output=True, text=True)
            st["black_errors"] = p.stderr.count("error:")
            for c in clones("orig"):
                for p in py_files(os.path.join(dst_root, c)):
                    st["files"] += 1
            stats[variant] = dict(st)
            continue
        for c in clones("orig"):
            src_root = os.path.join(DATA, "orig", c)
            bare = os.path.join(CLONES, c)
            for p in py_files(src_root):
                rel = os.path.relpath(p, src_root)
                with open(p, encoding="utf-8") as h:
                    text = h.read()
                out = text
                try:
                    if variant == "rename":
                        out, nl, renamed = rename_file(text, bare, rel)
                        st["locals_renamed"] += nl
                        st["functions_renamed"] += len(renamed)
                        if renamed:
                            ok, nk = keys_of(text), keys_of(out)
                            keymap.setdefault(c, {})[rel] = {b: a for a, b in zip(ok, nk) if a != b}
                    elif variant == "reorder":
                        out, moved = reorder_file(text)
                        st["defs_moved"] += moved
                        st["files_reordered"] += bool(moved)
                    elif variant == "unparse":
                        out = ast.unparse(ast.parse(text)) + "\n"
                except Exception as e:
                    st["failed_" + type(e).__name__] += 1
                    out = text
                st["files"] += 1
                st["files_changed"] += out != text
                dest = os.path.join(dst_root, c, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w", encoding="utf-8") as h:
                    h.write(out)
        with open(os.path.join(DATA, variant + ".keymap.json"), "w") as h:
            json.dump(keymap, h)
        stats[variant] = dict(st)
        print(variant, dict(st), file=sys.stderr)
    path = os.path.join(DATA, "transform-stats.json")
    old = json.load(open(path)) if os.path.exists(path) else {}
    old.update(stats)
    with open(path, "w") as h:
        json.dump(old, h, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:] or ["rename", "reorder", "unparse", "black"])

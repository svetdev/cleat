"""common — shared helpers: cleat's own extractors (imported read-only from the repo), the
attach-default config, and an AST index that gives every function a stable key across
transformations."""
import ast, json, os, sys, collections

sys.dont_write_bytecode = True
CLEAT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(CLEAT, "quality/bin"))
from extractors import complexity, patterns, duplication, languages  # noqa: E402
import ratchet  # noqa: E402

S = os.environ.get("STUDY_WORK", ".study-work")   # scratch: clones, trees, measurements
Q3 = os.path.join(S, "q3")
DATA = os.path.join(Q3, "data")
with open(os.path.join(DATA, "attach-quality.json")) as _h:
    ATTACH = json.load(_h)
CX = ATTACH["complexity"]
CC_CEIL, LINE_CEIL = CX["ceilings"]["cc"], CX["ceilings"]["lines"]
PY_PATTERNS = languages.language("python")["patterns"]


def clones(variant):
    root = os.path.join(DATA, variant)
    return sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))


def py_files(root):
    return list(patterns.files([root], [".py"], set(ATTACH["escapes"]["skip_dirs"])))


# ------------------------------------------------------------- function keys

def def_index(text):
    """[(start, end, qualkey)] for every def in `text`; qualkey = dotted qualname + '#n' (n-th
    def of that qualname in document order) — stable under reformat and reorder."""
    tree = ast.parse(text)
    out, seen = [], collections.Counter()

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = prefix + child.name
                if not isinstance(child, ast.ClassDef):
                    k = "%s#%d" % (q, seen[q])
                    seen[q] += 1
                    out.append((child.lineno, child.end_lineno, k))
                visit(child, q + ".")
            else:
                visit(child, prefix)
    visit(tree, "")
    return out


def enclosing(index, line):
    best = None
    for s, e, k in index:
        if s <= line <= e and (best is None or s >= best[0]):
            best = (s, k)
    return best[1] if best else "<module>"


def index_tree(root):
    """{relpath: def_index} for every .py file under root (unparseable files -> [])."""
    out = {}
    for p in py_files(root):
        rel = os.path.relpath(p, root)
        try:
            with open(p, encoding="utf-8") as h:
                out[rel] = def_index(h.read())
        except Exception:
            out[rel] = []
    return out


# ------------------------------------------------------------- measurements

def measure_complexity(root):
    """{(rel, qualkey): (cc, length, line)} via cleat's extractor with the attach config."""
    fns, _, tool, version = complexity.measure(CX, [root], [], root=root)
    idx = index_tree(root)
    real = os.path.realpath(root)
    out = {}
    for f in fns:
        rel = os.path.relpath(f.path, real)
        key = None
        for s, e, k in idx.get(rel, []):
            if s == f.line:
                key = k
                break
        if key is None:
            key = "@line%d:%s" % (f.line, f.name)
        out[(rel, key)] = (f.cc, f.length, f.line)
    return out, version, [x.entry() for x in findings_of(fns, root)]


def over(v):
    return v[0] > CC_CEIL or v[1] > LINE_CEIL


_CHECK = None


def findings_of(fns, root):
    """The gate's own Finding list over root (check-complexity.py's judge, loaded from the repo)."""
    global _CHECK
    import importlib.util
    if _CHECK is not None:
        mod = _CHECK
        return [ratchet.Finding(f, line, text, {"cc": cc, "lines": n})
                for f, line, text, cc, n in mod.judge(fns, CC_CEIL, LINE_CEIL, root)]
    spec = importlib.util.spec_from_file_location("check_complexity", os.path.join(CLEAT, "quality/bin/check-complexity.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _CHECK = mod
    return [ratchet.Finding(f, line, text, {"cc": cc, "lines": n})
            for f, line, text, cc, n in mod.judge(fns, CC_CEIL, LINE_CEIL, root)]


def escape_sites(root):
    """[(rel, line, text, kind)] — cleat's python patterns over root."""
    files = patterns.files([root], [".py"], set(ATTACH["escapes"]["skip_dirs"]))
    return list(patterns.sites(files, PY_PATTERNS, root))


def escape_findings(sites):
    """Sites folded the way check-escapes.py folds them: one Finding per (file, text), with a count."""
    seen = {}
    for rel, line, text, kind in sites:
        key = (rel, text)
        if key in seen:
            a, b, c = seen[key]
            seen[key] = (a, b, c + 1)
        else:
            seen[key] = (line, kind, 1)
    return [ratchet.Finding(rel, line, text, {"escape": kind, "count": c}) for (rel, text), (line, kind, c) in seen.items()]


def dup_measure(root):
    paths = sorted(py_files(root))
    cl = duplication.find(paths, root, int(ATTACH["duplication"]["min_lines"]))
    d, t = duplication.density(cl, paths, root)
    return cl, (d, t)

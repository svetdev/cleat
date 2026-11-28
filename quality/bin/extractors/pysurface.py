"""pysurface — Python's public surface, read from the syntax tree.

A line-by-line reader sees `def run(` and stops: the parameters are on the lines
below, so a change to them goes unseen and reflowing the list reads as a break.
This one parses the file. A signature is the declaration as `ast` writes it back,
whatever lines it was spread over: a module's functions, classes with their
bases, constants in capitals, and for each exported class its methods without an
underscore plus `__init__`, as `def Client.run(self, x)`.

A name counts when it has no leading underscore and is in `__all__` where there
is one. A module that is itself private (`_make.py`, anything under `_vendor/`)
exports only the names a public module imports from it, which is how a package
keeps its code in `_make.py` and its surface in `__init__.py`.

`widened()` says whether one signature differs from another only in what breaks
no call: an added parameter with a default, `*args` or `**kwargs`, or a
parameter's annotation. What it returns is still held to the letter.

A file `ast` cannot parse falls back to the line reader the caller hands in.
"""

import ast
import os
import re

from . import patterns

READER = "2"   # what a baseline written by this reader records as its version
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
CONSTANT = re.compile(r"[A-Z][A-Z0-9_]*$")
DEF = re.compile(r"(async def|def) ([\w.]+)(\(.*)$", re.S)


def _private(part):
    return part.startswith("_") and not (part.startswith("__") and part.endswith("__"))


def private_module(rel):
    """Whether a file is private by its own name or a directory's above it."""
    return any(_private(part) for part in rel[:-len(".py")].split(os.sep))


def _stem(rel):
    """The name a module is imported by: its file, or its directory for an `__init__.py`."""
    parts = rel[:-len(".py")].split(os.sep)
    return parts[-2] if parts[-1] == "__init__" and len(parts) > 1 else parts[-1]


def _text(node):
    return ast.unparse(node) if node is not None else None


def _def(node, owner=""):
    head = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = " -> " + ast.unparse(node.returns) if node.returns else ""
    return "%s %s%s(%s)%s" % (head, owner, node.name, ast.unparse(node.args), returns)


def _class(node):
    inside = [ast.unparse(b) for b in node.bases] + [ast.unparse(k) for k in node.keywords]
    return "class %s%s" % (node.name, "(%s)" % ", ".join(inside) if inside else "")


def _methods(node):
    return [(m.lineno, _def(m, node.name + ".")) for m in node.body
            if isinstance(m, FUNCTIONS) and (not m.name.startswith("_") or m.name == "__init__")]


def _constants(node):
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [t.id for t in targets if isinstance(t, ast.Name) and CONSTANT.match(t.id)]


def _declared(node, exported):
    """[(line, signature)] for one statement at module level."""
    if isinstance(node, FUNCTIONS):
        return [(node.lineno, _def(node))] if exported(node.name) else []
    if isinstance(node, ast.ClassDef):
        return [(node.lineno, _class(node))] + _methods(node) if exported(node.name) else []
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        return [(node.lineno, name) for name in _constants(node) if exported(name)]
    return []


def _all(tree):
    """The names `__all__` lists, or None when the module has none that can be read."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and "__all__" in [getattr(t, "id", None) for t in node.targets]:
            try:
                return set(ast.literal_eval(node.value))
            except (ValueError, TypeError):
                return None
    return None


def reexports(trees):
    """{module name: the names public modules import from it}, over every parsed file."""
    out = {}
    for rel, tree in trees.items():
        if private_module(rel):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                out.setdefault(node.module.split(".")[-1], set()).update(alias.name for alias in node.names)
    return out


def _module(rel, tree, shared):
    allowed = _all(tree)
    handed_on = shared.get(_stem(rel), set()) if private_module(rel) else None

    def exported(name):
        if name.startswith("_") or (allowed is not None and name not in allowed):
            return False
        return handed_on is None or "*" in handed_on or name in handed_on

    return [(rel, line, sig) for node in tree.body for line, sig in _declared(node, exported)]


def _parse(path):
    with open(path, errors="replace") as handle:
        try:
            return ast.parse(handle.read())
        except (SyntaxError, ValueError):
            return None


def built_in(roots, repo_root, skip_dirs, by_line):
    """[(repo-relative file, line, signature)] for every exported declaration under `roots`;
    `by_line(path)` reads a file that does not parse."""
    paths = {os.path.relpath(path, repo_root): path for path in patterns.files(roots, (".py",), skip_dirs)}
    trees = {rel: _parse(path) for rel, path in paths.items()}
    shared = reexports({rel: tree for rel, tree in trees.items() if tree})
    out = []
    for rel, tree in trees.items():
        out += _module(rel, tree, shared) if tree else by_line(paths[rel])
    return out


# ---------------------------------------------------------------- a change that breaks no caller

def _parsed(text):
    """(def or async def, dotted name, the function node) for a signature this reader wrote."""
    match = DEF.match(text)
    if not match:
        return None
    try:
        return match.group(1), match.group(2), ast.parse("def f%s: pass" % match.group(3)).body[0]
    except SyntaxError:
        return None


def _positional(args):
    """[(name, positional-only, default)], the default None where there is none."""
    params = args.posonlyargs + args.args
    defaults = [None] * (len(params) - len(args.defaults)) + list(args.defaults)
    return [(p.arg, n < len(args.posonlyargs), _text(d)) for n, (p, d) in enumerate(zip(params, defaults))]


def _keyword_only(args):
    return {p.arg: (_text(d),) for p, d in zip(args.kwonlyargs, args.kw_defaults)}


def _star(arg):
    return arg.arg if arg else None


def _optional(params, taken):
    """Added parameters no caller has to pass, where no catch-all was taking those arguments before."""
    return not params or (not taken and all(p[-1] is not None for p in params))


def _kept(old, new):
    """Every parameter the old signature had is where it was, and each catch-all too."""
    before, after = _keyword_only(old), _keyword_only(new)
    return (_positional(new)[:len(_positional(old))] == _positional(old)
            and all(after.get(name) == param for name, param in before.items())
            and _star(old.vararg) in (None, _star(new.vararg)) and _star(old.kwarg) in (None, _star(new.kwarg)))


def _added_optional(old, new):
    before, after = _keyword_only(old), _keyword_only(new)
    return (_optional(_positional(new)[len(_positional(old)):], old.vararg or old.kwarg)
            and _optional([after[name] for name in after if name not in before], old.kwarg))


def widened(old_text, new_text):
    """Whether `new_text` differs from `old_text` only in parameter annotations and in added
    parameters that no caller has to pass."""
    old, new = _parsed(old_text), _parsed(new_text)
    if not old or not new or old[:2] != new[:2] or old_text == new_text:
        return False
    a, b = old[2], new[2]
    return _text(a.returns) == _text(b.returns) and _kept(a.args, b.args) and _added_optional(a.args, b.args)

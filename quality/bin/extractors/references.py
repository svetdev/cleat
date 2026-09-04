"""references — which file depends on which, read two ways.

`identifiers` is for a language with no per-file import — Swift: every
top-level type name declared in the judged trees is owned by the file that
declares it, and a capitalised identifier in another file's code (comments and
strings stripped by regex) is a reference to that file. `imports` is for
languages with explicit imports — Python, TypeScript/JavaScript, Rust, Go,
Kotlin, Java: each import line is resolved to a file under one of the judged
roots. `ast-grep` is the identifiers reading done by a parser, for any of
nine languages: declarations are the grammar's declaration nodes, references
its identifier tokens, so a name in a comment or a string is never a
reference and no per-language regex is needed. All three yield the same
shape — (referencing file, line, name, referenced file, its root) — so the
layering and reachability judgments read any of them.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

from . import patterns

# ---------------------------------------------------------------- identifiers

DECL_RE = re.compile(
    r"^(?:@\w+(?:\([^)]*\))?\s+)*"
    r"(?:(?:public|internal|private|fileprivate|package|final|open|indirect|nonisolated)\s+)*"
    r"(?:struct|class|enum|actor|protocol|typealias)\s+([A-Z]\w*)",
    re.M,
)
IDENT_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\b")


def strip_code(text):
    """Code only: comments and string literals replaced, line count preserved."""
    def keep_lines(m):
        return "\n" * m.group(0).count("\n")
    text = re.sub(r"/\*.*?\*/", keep_lines, text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r'"""(?:.|\n)*?"""', lambda m: '""' + keep_lines(m), text)
    text = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', text)
    return text


def source_files(root, skip_dirs, suffixes=(".swift",)):
    return list(patterns.files([root], suffixes, skip_dirs))


def build_owner_index(owner_roots, skip_dirs, suffixes=(".swift",)):
    """(code, owner): `code` maps each file to its stripped text, `owner` maps each
    declared name to {root: declaring file} — one per root that declares it."""
    code = {}
    files_by_root = {}
    for root in owner_roots:
        files_by_root[root] = source_files(root, skip_dirs, suffixes)
        for path in files_by_root[root]:
            with open(path, errors="replace") as handle:
                code[path] = strip_code(handle.read())
    owner = {}
    for root in owner_roots:
        for path in files_by_root[root]:
            for name in DECL_RE.findall(code[path]):
                owner.setdefault(name, {}).setdefault(root, path)
    return code, owner


def resolve_owner(owner, name, own_root):
    """(declaring file, declaring root) for `name`, preferring `own_root`; None when
    the name is declared nowhere."""
    by_root = owner.get(name)
    if not by_root:
        return None
    if own_root in by_root:
        return by_root[own_root], own_root
    root, path = next(iter(by_root.items()))
    return path, root


def identifier_references(app, code, owner, skip_dirs, suffixes=(".swift",)):
    """Every (from file, line, name, owning file, owning root) in `app`, first line per
    pair, for references to names declared in another file."""
    references = []
    for path in source_files(app, skip_dirs, suffixes):
        seen = set()
        for number, line in enumerate(code[path].split("\n"), 1):
            for name in IDENT_RE.findall(line):
                resolved = resolve_owner(owner, name, app)
                if resolved is None or resolved[0] == path or name in seen:
                    continue
                seen.add(name)
                references.append((path, number, name, resolved[0], resolved[1]))
    return references


# ---------------------------------------------------------------- imports

IMPORT_RES = {
    "python": [re.compile(r"^\s*from\s+([\w.]+)\s+import\b", re.M),
               re.compile(r"^\s*import\s+([\w.]+)", re.M)],
    "typescript": [re.compile(r"""^\s*(?:import|export)\b[^'"\n]*?\bfrom\s*['"]([^'"]+)['"]""", re.M),
                   re.compile(r"""^\s*import\s*['"]([^'"]+)['"]""", re.M),
                   re.compile(r"""\b(?:require|import)\(\s*['"]([^'"]+)['"]\s*\)""")],
    "rust": [re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+((?:crate|super|self)(?:::\w+)+)", re.M),
             re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;", re.M)],
    "go": [re.compile(r'^\s*import\s+(?:\w+\s+)?"([^"]+)"', re.M),
           re.compile(r'^\s*(?:\w+\s+)?"([^"]+)"\s*$', re.M)],
    "kotlin": [re.compile(r"^\s*import\s+([\w.]+)", re.M)],
    "java": [re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)", re.M)],
}
IMPORT_RES["javascript"] = IMPORT_RES["typescript"]
SUFFIXES = {
    "python": (".py",), "typescript": (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"), "javascript": (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"),
    "rust": (".rs",), "go": (".go",), "kotlin": (".kt", ".kts"), "java": (".java",),
    "swift": (".swift",), "ruby": (".rb",), "csharp": (".cs",),
}


def _first_file(candidates):
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


def _resolve_python(spec, path, root):
    if spec.startswith("."):
        dots = len(spec) - len(spec.lstrip("."))
        base = os.path.dirname(path)
        for _ in range(dots - 1):
            base = os.path.dirname(base)
        rest = spec.lstrip(".").split(".") if spec.lstrip(".") else []
        return _first_file([os.path.join(base, *rest) + ".py", os.path.join(base, *rest, "__init__.py")])
    parts = spec.split(".")
    if parts and parts[0] == os.path.basename(root):
        parts = parts[1:]
    return _first_file([os.path.join(root, *parts) + ".py", os.path.join(root, *parts, "__init__.py")])


def _resolve_typescript(spec, path, root):
    if spec.startswith("."):
        base = os.path.normpath(os.path.join(os.path.dirname(path), spec))
    elif spec[:2] in ("@/", "~/"):
        base = os.path.join(root, spec[2:])
    else:
        return None
    stem = re.sub(r"\.(?:js|jsx|mjs|cjs)$", "", base)
    exts = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
    return _first_file([base] + [stem + e for e in exts] + [os.path.join(base, "index" + e) for e in exts])


def _resolve_rust(spec, path, root):
    if "::" not in spec:  # mod x;
        base = os.path.dirname(path)
        return _first_file([os.path.join(base, spec + ".rs"), os.path.join(base, spec, "mod.rs")])
    head, *parts = spec.split("::")
    if head == "crate":
        base = os.path.join(root, "src") if os.path.isdir(os.path.join(root, "src")) else root
    elif head == "super":
        base = os.path.dirname(os.path.dirname(path))
    else:
        base = os.path.dirname(path)
    for cut in range(len(parts), 0, -1):
        found = _first_file([os.path.join(base, *parts[:cut]) + ".rs", os.path.join(base, *parts[:cut], "mod.rs")])
        if found:
            return found
    return None


def _resolve_go(spec, path, root):
    parts = spec.split("/")
    for cut in range(1, len(parts) + 1):
        directory = os.path.join(root, *parts[-cut:])
        if os.path.isdir(directory):
            return os.path.abspath(directory)
    return None


def _resolve_jvm(spec, path, root):
    parts = spec.split(".")
    for drop_last in (0, 1):
        names = parts[:len(parts) - drop_last]
        for start in range(len(names)):
            for ext in (".kt", ".java"):
                found = _first_file([os.path.join(root, *names[start:]) + ext])
                if found:
                    return found
    return None


RESOLVERS = {"python": _resolve_python, "typescript": _resolve_typescript, "javascript": _resolve_typescript,
             "rust": _resolve_rust, "go": _resolve_go, "kotlin": _resolve_jvm, "java": _resolve_jvm}


def _under(target, roots):
    for root in roots:
        if target.startswith(os.path.join(os.path.abspath(root), "")):
            return root
    return None


def _name_of(spec):
    """What the import is called in a report: the last path segment without its
    extension, or the last dotted / `::` segment."""
    if "/" in spec:
        return os.path.splitext(spec.rstrip("/").split("/")[-1])[0] or spec
    return re.split(r"[.:]+", spec.strip(".:"))[-1] or spec


def _imports_in(path, language, roots, app):
    """(line, name, target, root) for each import in one file that resolves under a root."""
    with open(path, errors="replace") as handle:
        text = handle.read()
    out = []
    for regex in IMPORT_RES[language]:
        for match in regex.finditer(text):
            spec = match.group(1)
            target = RESOLVERS[language](spec, path, app)
            root = _under(target, roots) if target else None
            if root is not None and target != os.path.abspath(path):
                out.append((text.count("\n", 0, match.start()) + 1, _name_of(spec), target, root))
    return out


def import_references(app, roots, language, skip_dirs):
    """Every (from file, line, name, referenced file, its root) for the imports in `app`
    that resolve to a file under one of `roots`, first line per pair."""
    if language not in IMPORT_RES:
        raise KeyError("no import reader for %r — one of: %s" % (language, ", ".join(sorted(IMPORT_RES))))
    references = []
    for path in source_files(app, skip_dirs, SUFFIXES[language]):
        seen = set()
        for line, name, target, root in _imports_in(path, language, roots, app):
            if (name, target) not in seen:
                seen.add((name, target))
                references.append((path, line, name, target, root))
    references.sort(key=lambda r: (r[0], r[1]))
    return references


# ---------------------------------------------------------------- ast-grep

class ToolError(Exception):
    """ast-grep is missing or refused to run; the message says why."""


ASTGREP_TIMEOUT_SECONDS = int(os.environ.get("ASTGREP_TIMEOUT_SECONDS", "600"))
# Per language: the grammar's declaration node kinds, and its identifier token kinds.
# A declaration's name is the first identifier token inside it.
ASTGREP = {
    "python": (("class_definition", "function_definition"), ("identifier",)),
    "typescript": (("class_declaration", "abstract_class_declaration", "interface_declaration", "enum_declaration",
                    "type_alias_declaration", "function_declaration"), ("identifier", "type_identifier")),
    "javascript": (("class_declaration", "function_declaration"), ("identifier",)),
    "kotlin": (("class_declaration", "object_declaration", "function_declaration"), ("simple_identifier", "type_identifier")),
    "java": (("class_declaration", "interface_declaration", "enum_declaration", "record_declaration", "method_declaration"),
             ("identifier", "type_identifier")),
    "go": (("type_spec", "function_declaration", "method_declaration"), ("identifier", "type_identifier", "field_identifier")),
    "rust": (("struct_item", "enum_item", "trait_item", "function_item", "type_item"), ("identifier", "type_identifier")),
    "swift": (("class_declaration", "protocol_declaration", "function_declaration", "typealias_declaration"),
              ("simple_identifier", "type_identifier")),
    "ruby": (("class", "module", "method"), ("constant", "identifier")),
    "csharp": (("class_declaration", "interface_declaration", "struct_declaration", "enum_declaration", "method_declaration"),
               ("identifier",)),
}


def _astgrep_run(rule_path, root):
    """ast-grep's JSON matches for one rule file over `root`, or a ToolError."""
    try:
        proc = subprocess.run(["ast-grep", "scan", "--rule", rule_path, "--json=compact", "."],
                              capture_output=True, text=True, cwd=root, timeout=ASTGREP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise ToolError("ast-grep ran past its %ds time limit — ended" % ASTGREP_TIMEOUT_SECONDS)
    if proc.returncode != 0 and not proc.stdout.strip():
        raise ToolError("ast-grep exited %d: %s" % (proc.returncode, proc.stderr.strip()[:300]))
    return json.loads(proc.stdout or "[]")


def astgrep_scan(root, language, kinds):
    """[(abs file, line, byte start, byte end, text)] for every node of one of `kinds`
    under `root`, through `ast-grep scan`. Raises ToolError."""
    if not shutil.which("ast-grep"):
        raise ToolError("ast-grep is not installed — pip install ast-grep-cli (or brew install ast-grep)")
    rule = "id: cleat\nlanguage: %s\nrule:\n  any:\n%s" % (language, "".join("    - kind: %s\n" % k for k in kinds))
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as handle:
        handle.write(rule)
        rule_path = handle.name
    try:
        matches = _astgrep_run(rule_path, root)
    finally:
        os.unlink(rule_path)
    return [(os.path.abspath(os.path.join(root, m["file"])), m["range"]["start"]["line"] + 1,
             m["range"]["byteOffset"]["start"], m["range"]["byteOffset"]["end"], m["text"]) for m in matches]


def _skipped(path, root, skip_dirs):
    return any(part in skip_dirs for part in os.path.relpath(path, root).split(os.sep)[:-1])


def declared_symbols(decls, idents):
    """[(file, line, name)] per declaration: its name is the first identifier token
    inside it, in document order."""
    by_file = {}
    for path, _line, start, end, text in idents:
        by_file.setdefault(path, []).append((start, end, text))
    for path in by_file:
        by_file[path].sort()
    out = []
    for path, line, start, end, _text in sorted(decls, key=lambda d: (d[0], d[2])):
        name = next((text for s, e, text in by_file.get(path, []) if s >= start and e <= end), None)
        if name:
            out.append((path, line, name))
    return out


def _declared_names(decls, idents):
    """{name: file}: the first file that declares each name."""
    owner = {}
    for path, _line, name in declared_symbols(decls, idents):
        owner.setdefault(name, path)
    return owner


def _astgrep_index(roots, language, skip_dirs):
    """(owner, identifiers by root): every declared name's file per root, and every
    identifier token under each root, both with `skip_dirs` pruned."""
    decl_kinds, ident_kinds = ASTGREP[language]
    owner, idents_by_root = {}, {}
    for root in roots:
        decls = [d for d in astgrep_scan(root, language, decl_kinds) if not _skipped(d[0], root, skip_dirs)]
        idents_by_root[root] = [i for i in astgrep_scan(root, language, ident_kinds) if not _skipped(i[0], root, skip_dirs)]
        for name, path in _declared_names(decls, idents_by_root[root]).items():
            owner.setdefault(name, {}).setdefault(root, path)
    return owner, idents_by_root


def astgrep_references(app, roots, language, skip_dirs):
    """Every (from file, line, name, referenced file, its root) in `app` — a reference
    being an identifier token that names a declaration in another file under one of
    `roots`, the declaring file preferred under `app` when a name is declared in
    several roots. Raises KeyError for an unknown language, ToolError when ast-grep
    cannot run."""
    if language not in ASTGREP:
        raise KeyError("no ast-grep reader for %r — one of: %s" % (language, ", ".join(sorted(ASTGREP))))
    owner, idents_by_root = _astgrep_index(roots, language, skip_dirs)
    references, seen = [], set()
    for path, line, _start, _end, text in sorted(idents_by_root.get(app, [])):
        resolved = resolve_owner(owner, text, app)
        if resolved is None or resolved[0] == path or (path, text) in seen:
            continue
        seen.add((path, text))
        references.append((path, line, text, resolved[0], resolved[1]))
    return references

"""surface — a project's public API, as a set of signatures.

For a distributed library an accidental break is the costliest defect
shippable, and nothing in a build reads "public". `built_in()` reads the
declarations a language marks as exported — `export` in TypeScript, `pub` in
Rust, a capital letter in Go, `public`/`open` in Swift, `public` in Java and
Kotlin's default visibility, a name without an underscore (or in `__all__`) in
Python — each as (repo-relative file, line, signature), the signature being
the declaration line normalised up to its body. `from_cargo_public_api()` and
`from_api_extractor()` read the reports of tools that know their language
properly; use those when the project has them.
"""

import os
import re

from . import patterns

SUFFIXES = {
    "python": (".py",), "typescript": (".ts", ".tsx"), "rust": (".rs",), "go": (".go",), "swift": (".swift",),
    "kotlin": (".kt",), "java": (".java",),
}
# Per language: a regex over one line whose match is an exported declaration.
EXPORTED = {
    "python": re.compile(r"^(?:def|class|async def)\s+([A-Za-z]\w*)|^([A-Z][A-Z0-9_]*)\s*(?::|=)"),
    "typescript": re.compile(r"^export\s+(?!\{)(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?(?:class|interface|type|enum|function|const|let|var|namespace)\s+([A-Za-z_$][\w$]*)"),
    "rust": re.compile(r"^\s*pub\s+(?!\(crate\)|\(super\)|\(self\))(?:\([^)]*\)\s+)?(?:unsafe\s+|async\s+|const\s+)*(?:fn|struct|enum|trait|type|const|static|mod|union)\s+([A-Za-z_]\w*)"),
    "go": re.compile(r"^(?:func\s+(?:\([^)]*\)\s*)?|type\s+|var\s+|const\s+)([A-Z]\w*)"),
    "swift": re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:public|open)\s+(?:\w+\s+)*?(?:func|struct|class|enum|protocol|actor|var|let|typealias|init|subscript)\b\s*([A-Za-z_]\w*)?"),
    "kotlin": re.compile(r"^(?:public\s+)?(?!private|internal|protected)(?:\w+\s+)*?(?:class|interface|object|fun|val|var|typealias)\s+(?:<[^>]*>\s*)?([A-Za-z_]\w*)"),
    "java": re.compile(r"^\s*public\s+(?:\w+\s+)*?(?:class|interface|enum|record)\s+([A-Za-z_]\w*)|^\s*public\s+(?:static\s+|final\s+|abstract\s+|synchronized\s+)*[\w<>\[\], ?]+\s+([A-Za-z_]\w*)\s*\("),
}
ALL_RE = re.compile(r"^__all__\s*=\s*[\[(]([^\])]*)[\])]", re.M)


def _ends_signature(text, i, python):
    """Whether the character at `i` (outside any parentheses) opens the body."""
    ch = text[i]
    if ch == "{" or text.startswith("where ", i):
        return True
    if ch == "=":
        return text[i + 1:i + 2] != ">"
    return ch == ":" and python


def signature(line):
    """The declaration up to its body — a `{`, an `=`, a Python `:` or a `where`, each
    outside any parentheses so a default value stays part of the signature — with a
    trailing `;` or `:` dropped."""
    text = " ".join(line.strip().split())
    python = text.startswith(("def ", "class ", "async def "))
    depth = 0
    for i, ch in enumerate(text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and _ends_signature(text, i, python):
            text = text[:i]
            break
    return text.rstrip(" ;:")


def _python_names(text):
    match = ALL_RE.search(text)
    if not match:
        return None
    return set(re.findall(r"['\"]([A-Za-z_]\w*)['\"]", match.group(1)))


def _python_exported(name, allowed):
    """Python's convention: no leading underscore, and in __all__ when there is one."""
    if name is None or name.startswith("_"):
        return False
    return allowed is None or name in allowed


def _exported_name(match, language, allowed):
    """The declared name, or None when the language would not export it."""
    name = next((g for g in match.groups() if g), None)
    if language == "python":
        return name if _python_exported(name, allowed) else None
    return name or ""


def _file_surface(path, language, repo_root):
    with open(path, errors="replace") as handle:
        text = handle.read()
    allowed = _python_names(text) if language == "python" else None
    rel = os.path.relpath(path, repo_root)
    out = []
    for number, line in enumerate(text.split("\n"), 1):
        match = EXPORTED[language].match(line)
        if match and _exported_name(match, language, allowed) is not None:
            out.append((rel, number, signature(line)))
    return out


def built_in(roots, language, repo_root, skip_dirs=()):
    """[(repo-relative file, line, signature)] for every exported declaration."""
    out = []
    for path in patterns.files(roots, SUFFIXES[language], skip_dirs):
        out += _file_surface(path, language, repo_root)
    return out


def from_cargo_public_api(text):
    """[(file, line, signature)] from `cargo public-api` output — one item per line."""
    return [("cargo-public-api", n, " ".join(line.split())) for n, line in enumerate(text.splitlines(), 1) if line.strip()]


def from_api_extractor(text):
    """[(file, line, signature)] from an api-extractor `.api.md` report: the exported
    declarations inside its ```ts fences."""
    out, inside = [], False
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("```"):
            inside = not inside
            continue
        if inside and line.startswith("export "):
            out.append(("api-extractor", n, signature(line)))
    return out

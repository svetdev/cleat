"""parts — the pieces a repository is made of, and the debt each one carries.

A monorepo's backend and frontend are different code in different languages,
and one number over both says little about either. A part is a directory that
builds on its own: one holding a manifest (`package.json`, `Cargo.toml`,
`go.mod`, `pyproject.toml`, `Package.swift`, a Gradle or Maven build, a
Gemfile, an Xcode project). The topmost such directories below the root are
the parts; three or more siblings under one parent in one language
(`crates/*`, `packages/*`) are one part, since they are one codebase split for
the build's sake. What lies outside every part is the rest, `.`, when there is
enough of it to say anything about. A repository with no nested manifest is one
part. `"parts": [{"name", "roots"}]` in quality.json names them instead.

Each part's debt is read off the baselines by file — the entries under its
roots — and sized against its production lines, so a part can be compared with
other code in its own language (`reference.py`).
"""

import os

from extractors import census as census_
from extractors import patterns

MANIFESTS = {"package.json", "Cargo.toml", "go.mod", "pyproject.toml", "setup.py", "Package.swift",
             "build.gradle", "build.gradle.kts", "pom.xml", "Gemfile"}
MIN_LINES = 200       # a part smaller than this is folded into the rest
GROUP_AT = 3          # siblings in one language that make one part


def manifest_dirs(root, skip_dirs=()):
    """Every directory under `root` (repo-relative, `.` for the root) holding a manifest."""
    found = set()
    for dirpath, filenames in patterns.walk([root], skip_dirs):
        rel = os.path.relpath(dirpath, root)
        if any(p.startswith(".") for p in rel.split(os.sep) if p != "."):
            continue
        if MANIFESTS.intersection(filenames):
            found.add(rel)
        if dirpath.endswith(".xcodeproj"):
            found.add(os.path.dirname(rel) or ".")
    return found


def topmost(dirs):
    """The directories below the root that no other one (but the root) contains."""
    below = sorted(d for d in dirs if d != ".")
    return [d for d in below if not any(census_.under(d, [o]) for o in below if o != d)]


def dominant(census, files):
    langs = census_.by_language(census, files)
    return max(sorted(langs), key=langs.get) if langs else None


def _grouped(tops, census, production):
    """Sibling directories in one language, three or more of them, as one part `parent/*`."""
    groups = {}
    for d in tops:
        files = census_.read_by({f: census[f] for f in production}, [d])
        groups.setdefault((os.path.dirname(d), dominant(census, files)), []).append(d)
    parts = []
    for (parent, _), members in sorted(groups.items(), key=lambda kv: kv[1][0]):
        if len(members) >= GROUP_AT and parent:
            parts.append((parent + "/*", members))
        else:
            parts += [(d, [d]) for d in members]
    return parts


def detect(root, census, production, declared=None, skip_dirs=()):
    """[(name, [roots])], the root's own remainder last as `.`."""
    if declared:
        return [(p["name"], list(p["roots"])) for p in declared]
    found = _grouped(topmost(manifest_dirs(root, skip_dirs)), census, production)
    sized = [(name, roots) for name, roots in found if _lines_under(census, production, roots) >= MIN_LINES]
    if not sized:
        return [(".", ["."])]
    return sized + ([(".", ["."])] if census_.lines(census, _rest(production, sized)) >= MIN_LINES else [])


def _lines_under(census, production, roots):
    return census_.lines(census, {f for f in production if census_.under(f, roots)})


def _rest(production, parts):
    """The production files no named part claims."""
    others = [r for name, roots in parts if name != "." for r in roots]
    return {f for f in production if not census_.under(f, others)}


def files_of(name, roots, production, parts):
    """The production files of one part; `.` is what no other part claims."""
    if name == ".":
        return _rest(production, parts)
    return {f for f in production if census_.under(f, [os.path.normpath(r) for r in roots])}


def debt(gate_entries, files):
    """{check: {"total", "cc", "lines"}} summed over every baselined gate's entries in
    `files`; `gate_entries` is [(check, entries, spec)], a check's gates adding up."""
    out = {}
    for check, entries, spec in gate_entries:
        mine = _debt_of(check, [e for e in entries if e.get("file") in files], spec)
        into = out.setdefault(check, {})
        for key, value in mine.items():
            into[key] = into.get(key, 0) + value
    return out


def _debt_of(check, entries, spec):
    if check == "complexity":
        ceilings = spec.get("ceilings") or {}
        cc, n = ceilings.get("cc", 8), ceilings.get("lines", 60)
        return {"total": len(entries), "cc": sum(max(0, e.get("cc", 0) - cc) for e in entries),
                "lines": sum(max(0, e.get("lines", 0) - n) for e in entries)}
    if check == "crap":
        return {"total": len(entries)}
    return {"total": sum(e.get("count", 1) for e in entries)}


def metrics(census, files, debt_of_part):
    """The per-1k-line numbers a part is compared on, with its size and languages."""
    size = census_.lines(census, files)
    per = lambda value: round(1000.0 * value / size, 3) if size else None
    out = {"lines": size, "language": dominant(census, files), "languages": census_.by_language(census, files)}
    c = debt_of_part.get("complexity")
    if c is not None:
        out.update(complexity_per_kloc=per(c["total"]), over_cc_per_kloc=per(c["cc"]), over_lines_per_kloc=per(c["lines"]))
    for check in ("escapes", "crap", "conventions", "sarif"):
        if check in debt_of_part:
            out[check + "_per_kloc"] = per(debt_of_part[check]["total"])
    return out

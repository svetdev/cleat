#!/usr/bin/env python3
"""check-features-map — fail if the feature map cites a source file that does not
exist, or if a service under a swept root is reached by nothing.

The map (`"features_map"."file"` in `quality.json` — `docs/features.md` in the
repository this was written for) is the capability-to-test map, and CONTRIBUTING.md
points contributors at it and asks them to keep it current. Its Source and Test
columns name files in backticks; a row whose file was renamed or deleted keeps
reading as coverage that someone checked, because nothing ever re-reads the
path. Eleven of the paths it cited named nothing in the tree before this check
existed.

Every fact that names a project — which file the map is, where a citation may
resolve, which roots are swept for service reachability, which services are
frozen — is read from `quality.json` (`quality_config.py` beside this script
finds it, or `--config` names it); nothing project-specific is spelled here.

Three passes. The first two read the map; the third reads the tree.

Resolution. Every backticked token ending in `.swift` anywhere in the file,
resolved against `"roots"` — in this repo the app target, the two test targets,
and the host-less package's sources and tests. Those are the only places Swift
lives there, so a path that resolves under none of them names nothing, whatever
it looks like.

Reachability of an `[x]` row. A path resolving is not the same as a capability
existing: a service can sit in the tree, compile, be unit-tested, and be
constructed by nothing the app runs. Two rows were in that state when this pass
was written, both citing `Services/OTLPExporter.swift`; a third had cited
`Services/TeamTemplateSync.swift` until that service was deleted. So for a row
whose status cell is `[x]` citing a `Services/*.swift` path, the path is looked
up under each of `"service_roots"` in turn; once resolved, the file's top-level
`struct`/`class`/`enum`/`actor`/`protocol` names are read and looked for in the
*code* of the other sources swept from those same roots — the app target and
the host-less package both, so a package service named only from app code is
reached, and an app service named only from package code is too. A file none
of them names is reported. Declared names rather than the filename, because the
two often differ — `Services/ItemDossier.swift` declares `ItemDisagreements`,
and a filename match would call it unreachable. Code rather than the whole
text, because prose is not construction: this repo's headers name other
services constantly — `Services/SubprocessOutput.swift` names four it does not
call — so a mention counted from a comment makes a dormant service reachable by
being talked about. Comments are stripped before the reading
(`strip_comments`), string literals are not.

Reachability of a service. The map is not the tree, and the pass above judges
only what the map cites: a service no row mentions at all is in exactly the
state that pass exists to catch, and nothing reads it. Nine were, when this
pass was written — each compiled, each carrying tests, none constructed by
anything the app runs, so the suite tally counted coverage of code no run can
reach. So the same reading — code only, comments stripped — is run over every
`Services/*.swift` under each root in `"service_roots"`, read recursively,
cited or not — a service under a subdirectory of `Services/` (`Services/Control/`,
for instance) is judged the same as one at the top level, and the same
widening applies to the `[x]`-row pass above it, since both read through
`SERVICE_RE`. It is a ratchet: a service in that state is named in
`"exempt_services"`, keyed by its path relative to the repository root (so a
service under the app target and one under the package that happen to share a
`Services/` filename are still two distinct keys), with the reason it is
there, and the success line says how many are exempt, so adding one is a
decision somebody makes on purpose. The nine it was written for have since
been retired out of the tree, so the list is empty — which is the state it is
trying to reach, not a reason to relax.

An `"exempt_services"` entry that names a path no swept root has is itself a
failure: the tree moved on and left a stale key behind, which would silently
exempt whatever service is later added at that same path. This is reported
like the other two — the fix is to delete the entry, not to re-add a file at
its path.

An entry can also go stale the other way: the reachability pass above only
ever judges `checked`, the services not on the list, so an exempt service that
has since been wired into a path another swept source names is never looked at
again — the exemption stands forever, counted in the success line's exempt
figure as though the service were still unreachable, and would silently exempt
it again if it later went dormant. So `exempt` is run through the same reading
`checked` is, and a service the reading no longer calls unreached is reported —
by path, by the reason recorded for it, and by what now reaches it — the same
way a stale key is. The mentions that reading is allowed to count are
restricted to sources outside `"exempt_services"` first (`restricted_to`): two
still-exempt services naming each other -- one instrument now handing its
output to a second that nothing yet calls either -- have not wired anything
into a path the app runs, so neither retires the other's exemption. The fix
there too is to edit `"exempt_services"`, not the tree: delete the entry, or,
if it should stay exempt on purpose, update the reason to say why.

Both reachability passes are deliberately narrow. `Views/`, `Models/` and
`Runtime/` are reached in ways this cannot see (SwiftUI body composition, Codable
synthesis), and the test targets are not swept at all, so an `[x]` there is a
different judgement and is left alone — and a mention from a test source never
counts as reaching a service, whatever it cites.

What no pass judges: whether a named test actually exercises the feature beside
it. That is not something a script can decide, and this one does not attempt it.

Exits non-zero and lists the offenders — with the line each sits on, for the
passes that read the map, since one path can be cited by several rows — if any
cited path resolves nowhere, any `[x]` service is unreached, any service under
a swept root is reached by nothing, any `"exempt_services"` entry names a path
no swept root has, or any `"exempt_services"` entry names a service another
swept source now reaches. Exits 2 when no `quality.json` can be found or the
one found lacks the `"features_map"` section.
No third-party dependencies (Python 3 stdlib only).

Usage
  quality/bin/check-features-map.py               # check the map quality.json names
  quality/bin/check-features-map.py --quiet       # only print on failure
  quality/bin/check-features-map.py --map PATH    # read another map file
  quality/bin/check-features-map.py --config PATH # run under another quality.json

quality.json
  "features_map": {
    "file": "docs/features.md",             the map
    "roots": ["Kiteloop/Kiteloop", …],      where a citation may resolve
    "service_roots": [                      swept for Services/ reachability
      "Kiteloop/Kiteloop",
      "Kiteloop/KiteloopCore/Sources/KiteloopCore"
    ],
    "exempt_services": {                    frozen services, keyed repo-relative
      "Kiteloop/Kiteloop/Services/Dormant.swift": "tracked as <item id>"
    }
  }
  Paths are relative to the directory quality.json is in.
"""

import argparse
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config  # noqa: E402

SECTION = "features_map"

# Backticked spans, so a path is only read where the map presents one. Prose
# mentions of a file outside backticks are not citations and are left alone.
CITATION_RE = re.compile(r"`([^`\n]+\.swift)`")

# A row's status cell, which is the first cell of a table row.
DONE_ROW_RE = re.compile(r"^\|\s*\[x\]\s*\|")

# The scope of the reachability pass: services, and only services -- read
# recursively, so a service under a subdirectory of Services/ (Services/Control/,
# for instance) is judged the same as one at the top level.
SERVICE_RE = re.compile(r"^Services/.+\.swift$")

# A top-level type declaration -- anchored at column 0, so a type nested inside
# another one is not read as a name the file offers to the rest of the app.
# Attributes and modifiers may precede the keyword (`@MainActor final class Foo`).
DECLARATION_RE = re.compile(
    r"^(?:(?:@[\w.]+(?:\([^)\n]*\))?|public|internal|private|fileprivate|open|final|package)\s+)*"
    r"(?:struct|class|enum|actor|protocol)\s+(\w+)",
    re.MULTILINE,
)

# Swift identifiers, for reading which names a source mentions.
IDENTIFIER_RE = re.compile(r"\b\w+\b")

# The start of a Swift string literal, tried at each character while stripping
# comments: any number of leading `#` for a raw string, then `"""` or `"` --
# the longer spelling first, so a multi-line opener is not read as an empty
# string followed by a quote.
STRING_OPEN_RE = re.compile(r'(#*)("""|")')

# A source swept for reachability: `label` is its root's spelling from
# `"service_roots"` (`Kiteloop/Kiteloop`, say), `directory` that root's
# resolved directory, `path` the file's path relative to it. Carrying the
# label with the file is what lets a report say which root a service lives
# under, and what lets two roots share a `Services/` filename without their
# reachability being judged as one file.
Service = collections.namedtuple("Service", ["label", "directory", "path"])


def repo_path(service):
    """A service's path relative to the repository root -- the spelling
    `"exempt_services"` is keyed by, and the one a FAIL line reports."""
    return os.path.join(service.label, service.path)


def join_and(items):
    """`["a"]` -> `"a"`; `["a", "b", "c"]` -> `"a, b and c"` -- for naming every
    swept root in one sentence without a trailing dangling comma."""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


class Settings:
    """What one run judges, read off a quality.json.

    The relative spellings (`roots`, `service_roots`) are kept beside the
    resolved directories because the messages print them: a reader is told
    `Kiteloop/Kiteloop/Services/`, not the absolute path of this checkout.

    `"exempt_services"` is the ratchet's list: the services that were already
    reached by nothing when the third pass was written. Each is compiled, each
    carries tests, and none is constructed by anything the app runs — which is
    the state the pass exists to report, so reporting them is what would keep it
    from ever running green. Freezing them in the file instead makes the
    situation visible and stops it growing: a service added to the list is a
    decision somebody took and wrote a reason for, and the success line prints
    how many are on it so the list cannot go quiet. Wiring one up, or deleting
    it, is the work — not an edit there. Where the backlog already tracks that
    work the item id is the reason. Keys are repo-relative rather than root-
    relative so a service under one swept root and one under another can share
    a `Services/` filename without colliding in the list. An entry naming a
    path no swept root has is a failure, not a no-op: main() reports it and
    exits non-zero.
    """

    def __init__(self, config, map_path=None):
        self.config_path = config.file
        self.repo = config.root
        self.features = os.path.abspath(map_path) if map_path else config.path(
            config.get(SECTION, "file")
        )
        self.roots = config.get(SECTION, "roots")
        self.root_dirs = config.paths(self.roots)
        self.service_roots = config.get(SECTION, "service_roots")
        self.service_root_dirs = config.paths(self.service_roots)
        self.exempt_services = config.get(SECTION, "exempt_services")


def citations(text):
    """Return (path, line number) for every backticked .swift path, in file order."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        for path in CITATION_RE.findall(line):
            found.append((path.strip(), number))
    return found


def service_rows(text):
    """Return (path, line number) for every `Services/*.swift` cited by an `[x]` row.

    The mark is the claim being checked, so a `[~]` or `[ ]` row -- which says the
    capability is not finished -- is not a candidate, whatever it cites.
    """
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not DONE_ROW_RE.match(line):
            continue
        for path in CITATION_RE.findall(line):
            path = path.strip()
            if SERVICE_RE.match(path):
                found.append((path, number))
    return found


def resolves(path, settings):
    return any(os.path.isfile(os.path.join(root, path)) for root in settings.root_dirs)


def sources_under(directory):
    """Every .swift file under `directory`, as directory-relative paths."""
    found = []
    for here, _, names in os.walk(directory):
        for name in names:
            if name.endswith(".swift"):
                found.append(os.path.relpath(os.path.join(here, name), directory))
    return sorted(found)


def swept_sources(settings):
    """Every .swift file under each of `"service_roots"`, as `Service`s.

    Root by root, in the order `"service_roots"` lists them, so a service's
    place in a printed count is stable across runs of the same config.
    """
    found = []
    for label, directory in zip(settings.service_roots, settings.service_root_dirs):
        for path in sources_under(directory):
            found.append(Service(label, directory, path))
    return found


def strip_comments(text):
    """Return `text` with its comments replaced by a space, string literals kept.

    Prose is not construction: a doc comment naming another service says only
    that somebody wrote the name down, and this repo's headers do that
    constantly -- `Services/SubprocessOutput.swift` names four services it does
    not call. Left in, a dormant service is reached the moment any file merely
    talks about it, which is the state both reachability passes exist to catch.

    A comment becomes one space rather than nothing, so `foo/*c*/bar` does not
    close up into an identifier neither half wrote.

    String literals are left in place: an interpolated segment carries real code
    (`\\(Foo.bar)`), and dropping literals would hide it. They are still *read*,
    because a `//` inside one -- a URL, say -- is not the start of a comment, and
    cutting there would delete the rest of the line's code and could report a
    genuinely reached service as an orphan. Block comments nest, as they do in
    Swift.
    """
    out = []
    index, end = 0, len(text)
    while index < end:
        if text.startswith("//", index):
            newline = text.find("\n", index)
            out.append(" ")
            index = end if newline < 0 else newline
            continue
        if text.startswith("/*", index):
            depth, index = 1, index + 2
            while index < end and depth:
                if text.startswith("/*", index):
                    depth, index = depth + 1, index + 2
                elif text.startswith("*/", index):
                    depth, index = depth - 1, index + 2
                else:
                    index += 1
            out.append(" ")
            continue
        opening = STRING_OPEN_RE.match(text, index)
        if opening:
            hashes, quote = opening.groups()
            closing, escape = quote + hashes, "\\" + hashes
            out.append(opening.group(0))
            index = opening.end()
            while index < end:
                if text.startswith(escape, index) and index + len(escape) < end:
                    # An escape takes the character after it with it, so a
                    # `\"` does not read as the end of the literal.
                    out.append(text[index : index + len(escape) + 1])
                    index += len(escape) + 1
                elif text.startswith(closing, index):
                    out.append(closing)
                    index += len(closing)
                    break
                else:
                    out.append(text[index])
                    index += 1
            continue
        out.append(text[index])
        index += 1
    return "".join(out)


def mention_index(sources):
    """Map each identifier a swept source mentions in code to the repo-relative
    paths of the sources mentioning it.

    Comments are stripped first -- see `strip_comments` -- so a name that appears
    only in prose is not counted as a source reaching the service that declares
    it. One index built from every source `swept_sources` found -- the app
    target and the host-less package both -- so a package service constructed
    only from app code is reached, and an app service constructed only from
    package code is too.

    Built once and read per candidate: the alternative -- searching the tree for
    each row's names -- rescans the whole sweep once per row.
    """
    index = {}
    for service in sources:
        with open(os.path.join(service.directory, service.path), errors="replace") as handle:
            text = strip_comments(handle.read())
        key = repo_path(service)
        for identifier in set(IDENTIFIER_RE.findall(text)):
            index.setdefault(identifier, set()).add(key)
    return index


def restricted_to(index, keys):
    """`index`, with every mentioning path outside `keys` dropped.

    For judging whether an exemption is retired: a mention from another still-
    exempt service does not wire anything into a path the app runs -- it is one
    dormant file naming another, the two of them exempt for the same reason. Only
    a mention from a source outside `"exempt_services"` says the service has
    actually been picked up by something live, so the reading for that judgement
    is restricted to those paths before it is run through the same
    `unreached_among`/`reached_among` either pass uses.
    """
    return {name: keys_seen & keys for name, keys_seen in index.items()}


def declared_names(service):
    """The top-level type names a service's source declares."""
    with open(os.path.join(service.directory, service.path), errors="replace") as handle:
        return DECLARATION_RE.findall(handle.read())


def service_files(sources):
    """The `Services/*.swift` sources among `sources`, in the order given."""
    return [service for service in sources if SERVICE_RE.match(service.path)]


def resolve_service_root(path, settings):
    """The (label, directory) of the first swept root that has `path`, or None."""
    for label, directory in zip(settings.service_roots, settings.service_root_dirs):
        if os.path.isfile(os.path.join(directory, path)):
            return label, directory
    return None


def unreached_among(services, index):
    """Return (service, names) for each service in `services` no *other* swept
    source names.

    A file declaring no top-level type offers no name to look for, so nothing can
    be concluded about it and it is not reported.

    Both reachability passes read through here, so a service an `[x]` row cites
    and a service the map never mentions are judged by one reading rather than by
    two that are free to drift apart.
    """
    offenders = []
    for service in services:
        names = declared_names(service)
        if not names:
            continue
        key = repo_path(service)
        if any(index.get(name, set()) - {key} for name in names):
            continue
        offenders.append((service, names))
    return offenders


def reached_among(services, index):
    """Return (service, names, reached_by) for each service in `services` that
    *is* named by another swept source -- the mirror of `unreached_among`, read
    through the same function so the two cannot drift apart on what counts as a
    name or a mention.

    A file declaring no top-level type is skipped here too, for the reason
    `unreached_among` skips it: it offers no name to look for, so nothing can be
    concluded about it either way -- it is neither reached nor unreached.
    """
    still_unreached = {service for service, _ in unreached_among(services, index)}
    found = []
    for service in services:
        if service in still_unreached:
            continue
        names = declared_names(service)
        if not names:
            continue
        key = repo_path(service)
        reached_by = set()
        for name in names:
            reached_by |= index.get(name, set()) - {key}
        found.append((service, names, sorted(reached_by)))
    return found


def service_row_candidates(text, settings):
    """Return (Service, line number) for every `[x]`-row `Services/*.swift`
    citation that resolves under a swept root.

    A citation that resolves only under a test root, or under none of the
    roots `"roots"` tries, is not a candidate for this pass — it may still be
    judged by the resolution pass above, but there is no service source here to
    read for names.
    """
    candidates = []
    for path, line in service_rows(text):
        found = resolve_service_root(path, settings)
        if found is None:
            continue
        label, directory = found
        candidates.append((Service(label, directory, path), line))
    return candidates


def unreached(candidates, index):
    """Return (path, names, line number) for each candidate no other swept
    source names.

    One entry per row rather than per file: a path two `[x]` rows cite is two
    claims, and a reader fixing one of them needs to be told about the other.

    `"exempt_services"` is not consulted here. An exemption freezes a service the
    map says nothing about; an `[x]` is somebody writing down that the capability
    is finished, and that claim is wrong whatever this script has agreed to
    tolerate elsewhere.
    """
    if not candidates:
        return []
    offenders = dict(unreached_among([service for service, _ in candidates], index))
    return [
        (service.path, offenders[service], line)
        for service, line in candidates
        if service in offenders
    ]


def display(path, repo):
    """Name a file the way a reader can click it: repo-relative, or absolute.

    `--map` can point outside the checkout, and a relative path there is a run of
    `../` that says less than the real one.
    """
    relative = os.path.relpath(path, repo)
    return path if relative.startswith(os.pardir) else relative


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true", help="only print on failure")
    ap.add_argument(
        "--map",
        metavar="PATH",
        help='feature map to read (default: "features_map"."file" in quality.json)',
    )
    quality_config.add_config_argument(ap)
    args = ap.parse_args()

    try:
        settings = Settings(quality_config.load(args.config), args.map)
    except KeyError as problem:
        print(f"FAIL: {problem.args[0]}", file=sys.stderr)
        return 2
    repo = settings.repo
    exempt_services = settings.exempt_services
    shown_map = display(settings.features, repo)
    shown_config = display(settings.config_path, repo)

    with open(settings.features) as handle:
        text = handle.read()
    cited = citations(text)

    missing = [(path, line) for path, line in cited if not resolves(path, settings)]

    # One index for both reachability passes: it is a read of every source the
    # sweep found, and building it twice would answer the same question twice.
    sources = swept_sources(settings)
    index = mention_index(sources)

    candidates = service_row_candidates(text, settings)
    unwired = unreached(candidates, index)

    services = service_files(sources)
    exempt = [service for service in services if repo_path(service) in exempt_services]
    checked = [service for service in services if repo_path(service) not in exempt_services]
    orphans = unreached_among(checked, index)

    # A key naming no service the sweep found cannot have been judged by the
    # pass above, and cannot be counted as exempt either -- `exempt` above
    # already excludes it, since it is built from `services`, not from the
    # list. Left unreported, it exempts whatever service is later added at
    # that path.
    known = {repo_path(service) for service in services}
    stale_exemptions = [
        (path, exempt_services[path])
        for path in sorted(exempt_services)
        if path not in known
    ]

    # An exemption is a claim that nothing reaches the service -- and unlike the
    # `checked` services, the pass above never looks at an exempt one again to
    # see whether that is still true. Running `exempt` through the same reading
    # catches the service that has since been wired into a path another swept
    # source names: the exemption is still on the list, still counted in the
    # success line's exempt figure, and would silently exempt the service again
    # if it later went dormant. The reading is restricted to sources outside
    # "exempt_services" -- see `restricted_to` -- so two exempt services naming
    # each other, both still unpicked-up by anything live, do not retire one
    # another. This is every swept source but the exempt ones, not just
    # `checked`: an entry point like `KiteloopApp.swift` is swept and can name a
    # service directly, and it is not itself a `Services/*.swift` candidate, so
    # it is never in `checked` -- excluding it here would silently drop the
    # commonest way a service actually gets wired in.
    live_keys = {repo_path(service) for service in sources} - set(exempt_services)
    live_index = restricted_to(index, live_keys)
    retired_exemptions = sorted(
        reached_among(exempt, live_index), key=lambda found: repo_path(found[0])
    )

    if not missing and not unwired and not orphans and not stale_exemptions and not retired_exemptions:
        if not args.quiet:
            # Name the scope beside each count: the claim is that every cited
            # path resolves, that no `[x]` service is unreached and that no
            # service outside the exemption list is, not that the map is
            # otherwise accurate. The exempt count is part of that scope — it is
            # how much of the third pass is not being run. The judged count is
            # the rows this pass actually read a service for, not the rows the
            # map happens to cite — a citation that resolves only under a test
            # root is not among them.
            where = ", ".join(f"{root}/" for root in settings.roots)
            per_root = join_and(
                [
                    f"{len([s for s in checked if s.label == label])} services under "
                    f"{label}/Services/"
                    for label in settings.service_roots
                ]
            )
            print(
                f"OK: all {len(cited)} .swift paths cited by "
                f"{shown_map} exist under {where}; "
                f"each of the {len(candidates)} services an [x] row cites "
                f"is named by another source under a swept root; "
                f"so is each of {per_root} ({len(exempt)} exempt)"
            )
        return 0

    if missing:
        print(
            f"FAIL: {len(missing)} .swift path(s) cited by "
            f"{shown_map} exist nowhere.\n"
            "A row naming a file that is gone reads as coverage nobody checked.\n",
            file=sys.stderr,
        )
        for path, line in missing:
            print(f"    {shown_map}:{line}: {path}", file=sys.stderr)
        print(
            "\nPoint each row at the file the capability lives in today, or delete the row\n"
            "if the capability is gone. Do not leave a Source column empty.",
            file=sys.stderr,
        )

    if unwired:
        if missing:
            print(file=sys.stderr)
        print(
            f"FAIL: {len(unwired)} [x] row(s) in {shown_map} cite a service "
            "no source under a swept root names.\n"
            "A capability nothing constructs is not implemented, whatever the mark says.\n",
            file=sys.stderr,
        )
        for path, names, line in unwired:
            print(
                f"    {shown_map}:{line}: {path} "
                f"(declares {', '.join(names)})",
                file=sys.stderr,
            )
        print(
            "\nWire the service into a path a swept root runs, or re-mark the row [~] and say\n"
            "where the wiring work is tracked. Do not leave it reading as shipped.",
            file=sys.stderr,
        )

    if orphans:
        if missing or unwired:
            print(file=sys.stderr)
        # No line numbers here: these come from the tree rather than from a row,
        # so the path is the whole address.
        where = join_and([f"{label}/Services/" for label in settings.service_roots])
        print(
            f"FAIL: {len(orphans)} service(s) under {where} are named by "
            "no other swept source.\n"
            "A service the app constructs nowhere is compiled and tested and cannot run,\n"
            "so a suite that covers it is counting coverage of code no run reaches.\n",
            file=sys.stderr,
        )
        for service, names in orphans:
            print(
                f"    {repo_path(service)} (declares {', '.join(names)})",
                file=sys.stderr,
            )
        print(
            "\nWire each into a path a swept root runs, or delete it. If it must stay dormant\n"
            f'for now, add it to "exempt_services" in {shown_config} with the\n'
            "reason and the item that tracks the wiring.",
            file=sys.stderr,
        )

    if stale_exemptions:
        if missing or unwired or orphans:
            print(file=sys.stderr)
        print(
            f'FAIL: {len(stale_exemptions)} "exempt_services" entry(ies) in '
            f"{shown_config} name a path\n"
            "no swept root has.\n"
            "A stale entry cannot have been judged, and would silently exempt whatever\n"
            "service is later added at that same path.\n",
            file=sys.stderr,
        )
        for path, reason in stale_exemptions:
            print(f"    {path}: {reason}", file=sys.stderr)
        print(
            "\nDelete the entry -- the tree has moved on, not the reason it was added.\n"
            "Do not re-add a file just to keep the entry valid.",
            file=sys.stderr,
        )

    if retired_exemptions:
        if missing or unwired or orphans or stale_exemptions:
            print(file=sys.stderr)
        print(
            f'FAIL: {len(retired_exemptions)} "exempt_services" entry(ies) in '
            f"{shown_config} name a service\n"
            "another swept source now reaches.\n"
            "An exemption is a claim that nothing constructs the service; once something does,\n"
            "leaving the entry in place counts it as unreachable forever and would silently\n"
            "exempt it again if it later went dormant.\n",
            file=sys.stderr,
        )
        for service, names, reached_by in retired_exemptions:
            path = repo_path(service)
            print(
                f"    {path}: {exempt_services[path]} "
                f"(declares {', '.join(names)}; reached by {', '.join(reached_by)})",
                file=sys.stderr,
            )
        print(
            "\nDelete the entry -- the service is wired in now, not still dormant. If it\n"
            "should stay exempt anyway, the reason needs updating to say why.",
            file=sys.stderr,
        )

    return 1


if __name__ == "__main__":
    sys.exit(main())

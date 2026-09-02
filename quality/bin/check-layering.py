#!/usr/bin/env python3
"""check-layering — fail if a source references a type from a layer it may not
depend on, unless the reference is on the project's exemption list.

Each judged tree is laid out in layers, by top-level directory under its own
root — `layering.roots` in `quality.json`, a list of `{"root", "exempt"}` — and
the layering is what lets a type be understood without reading the rest of the
tree: a model is a value, a service does work over models, a view model drives
a view. Nothing enforced it in the project this check was written for, and it
drifted the way unenforced structure does — a model read the global config
store, a service reached into a view model, and a model that needs a service
cannot be moved out, tested without it, or read on its own. Thirty-three such
references stood in the app when the check was written; the check judged the
app alone until the tree it moves reference-heavy files into, the local Swift
package, got a root of its own too — a file that crosses out of the app by
moving into the package cannot leave the ratchet just by moving.

What may depend on what is `layering.allowed` in `quality.json`, shared by
every root: each layer name maps to the layers it may reference besides
itself, or to null for a layer that may reach anything. In the project this
was written for that reads: Extensions on nothing; Models on Extensions;
Runtime on Models; Services on Runtime and Models; ViewModels on Services and
below; Views and the app entry (files at the root, layer `App`) on anything.
`layering.always_allowed` names layers reachable from every other — there,
`Testing/`, the "is this process the XCTest host" switch, since it is a fact
about the process, not a layer. Directories in `layering.skip_dirs` are not
read at all.

How a reference is read (`extractors/references.py`) depends on
`layering.references`: `identifiers` — the default, for Swift — reads every
top-level `struct`/`class`/`enum`/`actor`/`protocol`/`typealias` name declared
in the judged trees as owned by the file that declares it, and a capitalised
identifier in another file's *code* that matches one as a reference to that
file; `imports`, with `layering.language` one of python, typescript,
javascript, rust, go, kotlin, java, resolves each import line to the file it
names under a judged root; `ast-grep` is the identifiers reading through a
parser, for nine languages, needing `ast-grep` installed. For `identifiers`: Comments and string literals are
stripped first, because prose is not a dependency — a repo's headers name
types in other layers constantly, and a mention in a comment would make every
file depend on what it talks about. The reading is by name, so a nested type
that shadows a top-level name elsewhere reads as the top-level one; the line
reported lets a person see which it was.

It is a ratchet, per root. Every reference that violated the rules when its
root was added is in that root's `exempt` — a list of `{"file", "name",
"reason"}` objects, the file relative to the root, the name the referenced
type, the reason what it would take to remove it — so the check landed green
for that root and one more is a decision somebody makes on purpose. The
success line prints, for each root, how many are exempt beside how many
references were judged, so no root's list can grow quietly. An entry that no
longer names a violation — the reference was removed, or the file was — fails
the check too, in the root it was written for: a dead exemption is the same
rot one level up, and the way to a shorter list is to delete the entry the
moment the reference goes. An exemption belongs to one root; it is never read
against another root's tree, so a reference moved from one judged root to the
other fails there rather than quietly inheriting the old root's exemption. To
add an entry: the reference must already exist and violate the rules in that
root, since the check fails on an entry that names nothing.

It reads the tree and starts no build, so it is safe while the app is running.

  ./quality/bin/check-layering.py
  ./quality/bin/check-layering.py --quiet        # only print on failure
  ./quality/bin/check-layering.py --app DIR      # judge one handed-in tree instead of the configured roots (the tests use this)
  ./quality/bin/check-layering.py --config FILE  # the quality.json to read the rules from
"""

import argparse
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config

from extractors import references


class Root:
    """One judged tree: its absolute path and its own exemption list."""

    def __init__(self, path, exempt):
        self.path = path
        # (referencing file, referenced type) -> the reason it stands. Paths are
        # relative to this root.
        self.exempt = exempt


class Rules:
    """The `layering` section of a quality.json, read into the shapes the judge uses."""

    def __init__(self, config):
        self.skip_dirs = set(config.get("layering", "skip_dirs"))
        # Layer -> the layers it may reference, besides itself. None means anything.
        self.allowed = {
            layer: None if layers is None else set(layers)
            for layer, layers in config.get("layering", "allowed").items()
        }
        # Reachable from every layer: a fact about the process, not a layer.
        self.always_allowed = set(config.get("layering", "always_allowed"))
        self.roots = [self._root(config, entry) for entry in config.get("layering", "roots")]
        section = config.section("layering")
        # How a reference is read: `identifiers` (Swift — declared type names) or
        # `imports` (a language with explicit imports), with `language` naming which.
        self.reader = section.get("references", "identifiers")
        self.language = section.get("language", "swift")
        self.extensions = tuple(section.get("extensions", [".swift"]))
        if self.reader not in ("identifiers", "imports", "ast-grep"):
            raise KeyError('%s: "layering.references" must be "identifiers", "imports" or "ast-grep"; got %r' % (config.file, self.reader))

    def permits(self, from_layer, to_layer):
        """Whether `from_layer` may reference `to_layer`."""
        allowed = self.allowed.get(from_layer)
        return allowed is None or to_layer == from_layer or to_layer in allowed or to_layer in self.always_allowed

    @staticmethod
    def _root(config, entry):
        try:
            root, raw_exempt = entry["root"], entry["exempt"]
        except (KeyError, TypeError):
            raise KeyError('%s: every "layering.roots" entry needs "root" and "exempt"; got %r'
                            % (config.file, entry))
        exempt = {}
        for item in raw_exempt:
            try:
                exempt[(item["file"], item["name"])] = item["reason"]
            except (KeyError, TypeError):
                raise KeyError('%s: every "layering.exempt" entry needs "file", "name" and "reason"; got %r'
                               % (config.file, item))
        return Root(os.path.abspath(config.path(root)), exempt)


def layer_of(relative):
    parts = relative.split(os.sep)
    return parts[0] if len(parts) > 1 else "App"


def judge(app, exempt, rules, strict_dead, references_in_app):
    """(violations, dead exemptions, judged, exempt used) for the tree at `app`,
    judged against `exempt` — `strict_dead` when a file an entry names but the
    tree lacks still counts as dead (the tree is one of the configured roots,
    not a fixture handed in with --app, where a missing file is almost always
    just a fixture that has no reason to reproduce every file in the list).
    `references_in_app` is what the extractor read: (from file, line, name,
    referenced file, its root)."""
    references = references_in_app
    violations = []
    exempt_used = set()
    for path, number, name, target, target_root in references:
        source = os.path.relpath(path, app)
        if rules.permits(layer_of(source), layer_of(os.path.relpath(target, target_root))):
            continue
        key = (source, name)
        if key in exempt:
            exempt_used.add(key)
            continue
        violations.append((source, number, name, os.path.relpath(target, target_root)))
    dead = sorted(
        key for key in set(exempt) - exempt_used
        if strict_dead or os.path.isfile(os.path.join(app, key[0]))
    )
    return violations, dead, len(references), len(exempt_used)


def targets_for(args, rules, config):
    """What to judge: with --app, one handed-in tree under every root's exemptions at
    once and no dead-exemption strictness (a fixture stands in for whichever root it is
    shaped like); else each configured root under its own list."""
    if args.app:
        merged = {}
        for root in rules.roots:
            merged.update(root.exempt)
        app = os.path.abspath(args.app)
        return [(app, merged, False, None)], [app]
    targets = [(root.path, root.exempt, True, os.path.relpath(root.path, config.root)) for root in rules.roots]
    return targets, [root.path for root in rules.roots]


def reader_for(rules, owner_roots):
    """A function app → references, for the configured reader. Owners come from every
    judged root at once, so a name that moved from one root into another still resolves."""
    if rules.reader == "imports":
        if rules.language not in references.IMPORT_RES:
            raise KeyError("no import reader for %r — one of: %s" % (rules.language, ", ".join(sorted(references.IMPORT_RES))))
        return lambda app: references.import_references(app, owner_roots, rules.language, rules.skip_dirs)
    if rules.reader == "ast-grep":
        if rules.language not in references.ASTGREP:
            raise KeyError("no ast-grep reader for %r — one of: %s" % (rules.language, ", ".join(sorted(references.ASTGREP))))
        return lambda app: references.astgrep_references(app, owner_roots, rules.language, rules.skip_dirs)
    code, owner = references.build_owner_index(owner_roots, rules.skip_dirs, rules.extensions)
    return lambda app: references.identifier_references(app, code, owner, rules.skip_dirs, rules.extensions)


def main():
    parser = argparse.ArgumentParser(description="fail on a cross-layer reference that is not on the exemption list")
    parser.add_argument("--quiet", action="store_true", help="only print on failure")
    parser.add_argument("--app", help="judge one handed-in tree instead of the configured roots (the tests use this)")
    quality_config.add_config_argument(parser)
    args = parser.parse_args()
    config = quality_config.load(args.config)
    try:
        rules = Rules(config)
    except KeyError as problem:
        print("FAIL: %s" % problem.args[0])
        return 2

    targets, owner_roots = targets_for(args, rules, config)
    try:
        read = reader_for(rules, owner_roots)
    except KeyError as problem:
        print("FAIL: %s" % problem.args[0])
        return 2

    failed = False
    for app, exempt, strict_dead, label in targets:
        try:
            found = read(app)
        except references.ToolError as problem:
            print("FAIL: %s" % problem, file=sys.stderr)
            return 2
        violations, dead, judged, exempt_used = judge(app, exempt, rules, strict_dead, found)
        failed = print_verdict(violations, dead, judged, exempt_used, label, config, args.quiet) or failed
    return 1 if failed else 0


def print_violations(violations, where, config):
    print("FAIL: %d cross-layer reference(s) the layering does not allow%s." % (len(violations), where))
    print("Layers may depend downward only — see `layering.allowed` in %s." % config.file)
    for source, number, name, target in violations:
        print("  %s:%d -> %s (%s)" % (source, number, name, target))
    print("Move the type to the layer that needs it, inject it, or — on purpose — add the pair to the "
          "`exempt` list%s of the `layering` section in %s, with the reason." % (where, config.file))


def print_dead(dead, label, config):
    print("FAIL: %d EXEMPT entr%s in %s%s name%s no violating reference — the reference is gone, so the entry must go too:"
          % (len(dead), "y" if len(dead) == 1 else "ies", config.file,
             " for %s" % label if label else "", "s" if len(dead) == 1 else ""))
    for source, name in dead:
        print("  (%r, %r)" % (source, name))


def print_verdict(violations, dead, judged, exempt_used, label, config, quiet):
    """Print one root's result; True when it failed."""
    failed = bool(violations or dead)
    if violations:
        print_violations(violations, " in %s" % label if label else "", config)
    if dead:
        print_dead(dead, label, config)
    if not failed and not quiet:
        prefix = "%s: " % label if label else ""
        print("OK: %s%d cross-file references obey the layering (%d exempt)" % (prefix, judged, exempt_used))
    return failed


if __name__ == "__main__":
    sys.exit(main())

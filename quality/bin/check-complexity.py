#!/usr/bin/env python3
"""check-complexity — the complexity ratchet: a production function over the
cyclomatic ceiling or the length ceiling fails, beyond the ones the baseline
already holds — and a baselined one that grew fails too.

Agents write long functions and do not notice; neither does a reviewer reading
a diff, since the function was already long before the diff. A threshold a tool
enforces is the one kind of rule an agent cannot soften.

The reader is `extractors/complexity.py`: lizard for Python, TypeScript, Rust,
Go, Kotlin, Java, Ruby, Swift and more, or SwiftLint for Swift natively. The
ratchet is `ratchet.py`: functions over the gate on adoption day are in the
baseline, keyed by file and declaration text so a shifted line still matches,
with the numbers they had; a new one fails, a baselined one that grew fails,
one that improved or vanished is a NOTE — and a --strict failure, which is how
CI keeps the baseline exactly as loose as the code. A function renamed, its
body the same in shape, is the same function: a rename is not new debt.

With `"helpers": "inline"`, a function's cyclomatic count includes the helpers
only it calls — a function under both ceilings whose name appears once outside
its declaration across the sources, inside this one — so moving branches into a
single-use helper does not lower the number; making the logic shared, or
simpler, does. attach.py sets it for a new project; a config without the key
counts each function alone, as before.

Everything that names the project is the `complexity` section of quality.json
(`complexity_lizard` is read as the same thing, for configs written before the
two gates were one):

  "complexity": {
    "tool":      "lizard",                        # or "swiftlint"; lizard when "languages" is given
    "sources":   ["apps/api/src", "apps/web/src"],
    "languages": ["rust", "typescript"],          # lizard -l values
    "exclude":   ["*.test.ts"],                   # lizard -x globs
    "exclude_except": ["apps/api/src/test-runner.ts"],   # production paths an exclude glob would drop by name
    "skip_rust_tests": true,                      # drop `#[cfg(test)]` modules
    "ceilings":  {"cc": 8, "lines": 60},
    "helpers":   "inline",                        # count single-use helpers with their caller
    "baseline":  "quality/complexity-baseline.json"
  }

  quality/bin/check-complexity.py
  quality/bin/check-complexity.py --quiet
  quality/bin/check-complexity.py --write-baseline   # accept what is over the gate today
  quality/bin/check-complexity.py --strict           # CI: a loose baseline fails too
  quality/bin/check-complexity.py --csv FILE         # judge a saved lizard CSV (the tests use this)
  quality/bin/check-complexity.py --lint FILE        # judge a saved SwiftLint JSON report
"""

import argparse
import collections
import json
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_config
import ratchet
from extractors import languages, complexity, patterns

SECTIONS = ("complexity", "complexity_lizard")
RETIRED_KEYS = ("cwd", "config")   # the SwiftLint-native gate's shape, before the two gates were one


def is_retired_shape(section):
    return any(k in section for k in RETIRED_KEYS)


def section_of(config):
    """The complexity section, under either name — KeyError naming what is missing, what is
    retired, or the pair when both names configure a live gate."""
    present = [s for s in SECTIONS if isinstance(config.data.get(s), dict)]
    if not present:
        raise KeyError("%s has no \"complexity\" section — see quality.example.json" % config.file)
    live = [s for s in present if not is_retired_shape(config.data[s])]
    if len(live) > 1:
        raise KeyError("%s: both \"complexity\" and \"complexity_lizard\" configure this gate — keep one (they are the same section)" % config.file)
    if not live:
        raise KeyError("%s: \"%s\" is in the retired SwiftLint-native shape (cwd/config/a SwiftLint baseline). "
                       "Set \"tool\": \"swiftlint\", keep \"sources\" and \"ceilings\", point \"baseline\" at a new file "
                       "and write it with --write-baseline — or run attach.py --refresh, which rewrites it." % (config.file, present[0]))
    return live[0], config.data[live[0]]


def judge(functions, cc_ceiling, line_ceiling, repo, inlined=None):
    """[(repo-relative file, line, text, values)] for every function over either ceiling —
    its cyclomatic with its single-use helpers when `inlined` maps it to (cc, helpers)."""
    repo = os.path.realpath(repo)
    over = []
    for f in functions:
        cc, helpers = (inlined or {}).get(f, (f.cc, 0))
        if cc > cc_ceiling or f.length > line_ceiling:
            values = {"cc": cc, "lines": f.length, "body": ratchet.fingerprint(body_of(f))}
            if helpers:
                values.update(own_cc=f.cc, helpers=helpers)
            over.append((os.path.relpath(f.path, repo), f.line, complexity.declaration_text(f.path, f.line), values))
    over.sort(key=lambda o: (-max(o[3]["cc"] / cc_ceiling, o[3]["lines"] / line_ceiling), o[0], o[1]))
    return over


_LINES = {}


def lines_of(path):
    if path not in _LINES:
        try:
            with open(path, errors="replace") as handle:
                _LINES[path] = handle.read().split("\n")
        except OSError:
            _LINES[path] = []
    return _LINES[path]


def body_of(function):
    """The function's text, declaration to end, as the file has it."""
    end = getattr(function, "end", None) or function.line
    return "\n".join(lines_of(function.path)[function.line - 1:end])


def same_function(finding, entry):
    """A renamed function: a declaration of the same shape, and the same body — by fingerprint,
    or, for an entry written before fingerprints, by every value it recorded."""
    if ratchet.shape(finding.text) != ratchet.shape(entry["text"]):
        return False
    if entry.get("body") and finding.values.get("body"):
        return ratchet.resemblance(entry["body"], finding.values["body"]) >= 0.6
    return all(entry.get(m) == finding.values.get(m) for m in ("cc", "lines"))


NAME_RE = re.compile(r"[A-Za-z_$][\w$]*$")


def short_name(function):
    name = (function.name or "").split("::")[-1].split(".")[-1]
    return name if NAME_RE.match(name) else None


def spans(function, line):
    return function.line <= line <= (function.end or function.line)


def uses_of(name, path, declared):
    """The lines of `path`, other than the declaration's, that name `name` as a word."""
    word = re.compile(r"(?<![\w$])%s(?![\w$])" % re.escape(name))
    return [n for n, text in enumerate(lines_of(path), 1) if n != declared and word.search(text)]


def caller_of(helper, here, words):
    """The one function in `here` (the helper's file) that uses `helper`, when its name
    appears once outside its declaration across the sources; else None."""
    name = short_name(helper)
    uses = uses_of(name, helper.path, helper.line) if words[name] == 2 else []
    if len(uses) != 1:
        return None
    around = [c for c in here if c is not helper and spans(c, uses[0])]
    return min(around, key=lambda c: (c.end or c.line) - c.line) if around else None


def single_use_helpers(functions, cc_ceiling, line_ceiling, paths):
    """{caller: [helper]}: a helper is a named function under both ceilings that one other
    function in its file uses and nothing else across `paths` names."""
    words = collections.Counter()
    for path in paths:
        words.update(ratchet.IDENTIFIER_RE.findall("\n".join(lines_of(path))))
    by_file = collections.defaultdict(list)
    for f in functions:
        by_file[f.path].append(f)
    helpers = collections.defaultdict(list)
    for f in functions:
        caller = short_name(f) and f.cc <= cc_ceiling and f.length <= line_ceiling and caller_of(f, by_file[f.path], words)
        if caller:
            helpers[caller].append(f)
    return helpers


def inline_helpers(functions, cc_ceiling, line_ceiling, paths):
    """{function: (cyclomatic with its single-use helpers', how many helpers)} — each helper
    adding its own decisions (its count less the one every function starts with)."""
    helpers = single_use_helpers(functions, cc_ceiling, line_ceiling, paths)
    memo = {}

    def total(f, seen):
        if f not in memo:
            subs = [total(h, seen | {h}) for h in helpers.get(f, ()) if h not in seen]
            memo[f] = (f.cc + sum(cc - 1 for cc, _ in subs), sum(1 + n for _, n in subs))
        return memo[f]

    return {f: total(f, {f}) for f in functions}


def inlined_counts(section, args, config, name, functions, ceilings):
    """What `inline_helpers` makes of the functions when the section asks for it, else None.
    A saved report names no sources to read, so only its own files are."""
    if section.get("helpers") != "inline":
        return None
    sources = [] if args.csv or args.lint else config.paths(config.get(name, "sources"))
    paths = set(patterns.files(sources, language_suffixes(section))) | {f.path for f in functions}
    return inline_helpers(functions, ceilings[0], ceilings[1], paths)


def read_functions(args, name, section, config):
    """(functions, skipped, tool, version) from a saved report when a flag names one, else
    from the tool over the configured sources."""
    if args.csv:
        with open(args.csv) as handle:
            functions, skipped = complexity.functions_from_csv(handle.read(), skip_rust_tests=section.get("skip_rust_tests", True))
        return functions, skipped, "lizard", None
    if args.lint:
        with open(args.lint) as handle:
            return complexity.functions_from_swiftlint(json.load(handle)), 0, "swiftlint", None
    roots = config.paths(config.get(name, "sources"))
    if args.only is not None:
        roots = only_roots(args.only, roots, section, config)
        if not roots:
            return [], 0, complexity.tool_of(section), None
    return complexity.measure(section, roots, config.paths(section.get("exclude_except", [])), root=config.root)


def only_roots(only, sources, section, config):
    """The `--only` files worth measuring: on disk, under a configured source, and of
    a configured language. A changed file outside every source — a test tree,
    tooling the config never named — or a shell script under a source the
    config reads for Python has no baseline entries because it was never
    measured, and judging it here would read all of its standing debt as new."""
    return complexity.only_files(only, sources, language_suffixes(section), config.path)


def language_suffixes(section):
    """The file suffixes the section's tool reads: the configured languages' (the
    escapes table's spelling), or Swift's alone under SwiftLint."""
    names = section.get("languages") or []
    if not names:
        return (".swift",)
    return languages.suffixes(names)



def show_values(values):
    helpers = values.get("helpers")
    own = " (%s its own, with %d single-use helper%s)" % (values.get("own_cc"), helpers, "" if helpers == 1 else "s") if helpers else ""
    return "cc %s%s, %s lines" % (values["cc"], own, values["lines"])


def main():
    parser = argparse.ArgumentParser(description="complexity ratchet over the configured sources")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--csv", help="a saved lizard --csv output to judge instead of running a tool")
    parser.add_argument("--lint", help="a saved SwiftLint JSON report to judge instead of running a tool")
    parser.add_argument("--repo", help="paths are reported relative to this (default: the directory of quality.json)")
    ratchet.add_only_argument(parser)
    ratchet.add_tighten_argument(parser)
    ratchet.add_strict_argument(parser)
    quality_config.add_config_argument(parser)
    args = parser.parse_args()

    config = quality_config.load(args.config)
    try:
        name, section = section_of(config)
        ceilings = config.get(name, "ceilings")
        baseline_path = config.path(config.get(name, "baseline"))
        cc_ceiling, line_ceiling = int(ceilings["cc"]), int(ceilings["lines"])
        functions, skipped, tool, version = read_functions(args, name, section, config)
    except (KeyError, complexity.ToolError) as problem:
        print("FAIL: %s" % (problem.args[0] if problem.args else problem), file=sys.stderr)
        return 2

    repo = os.path.abspath(args.repo) if args.repo else config.root
    inlined = inlined_counts(section, args, config, name, functions, (cc_ceiling, line_ceiling))
    over = [ratchet.Finding(f, line, text, values)
            for f, line, text, values in judge(functions, cc_ceiling, line_ceiling, repo, inlined)]
    measured = ratchet.provenance(tool, version, {k: section[k] for k in sorted(section) if k != "baseline"})

    if args.write_baseline:
        ratchet.write(baseline_path, over, measured)
        print("baseline written: %d function(s) over the gate (cyclomatic > %d or body > %d lines)"
              % (len(over), cc_ceiling, line_ceiling))
        return 0

    entries, stored = ratchet.read(baseline_path)
    untouched = ratchet.outside(entries, args.only)
    over, entries = ratchet.restrict(over, entries, args.only)
    verdict = ratchet.judge(over, entries, ["cc", "lines"], stored, measured, renames=True, same=same_function)
    gate = ratchet.Gate(
        noun="production function(s)",
        over="over the complexity gate (cyclomatic > %d or body > %d lines)" % (cc_ceiling, line_ceiling),
        fix="Make the function simpler: fewer decisions, or logic shared with code that already needs it — "
            "not pieces only it calls. Accepting new debt into the baseline is a "
            "policy decision for a person, not a fix — see quality/README.md.",
        remedy="quality/bin/check-complexity.py --tighten",
        show=show_values)
    ok_line = ("OK: %d functions judged (%d inline tests skipped), %d over the gate, all %d in the baseline"
               % (len(functions), skipped, len(over), len(over)))
    return ratchet.report(verdict, gate, len(entries), ok_line, quiet=args.quiet, strict=args.strict,
                          tighten=args.tighten, baseline=(baseline_path, measured, untouched))


if __name__ == "__main__":
    sys.exit(main())

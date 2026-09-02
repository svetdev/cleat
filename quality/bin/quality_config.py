"""The per-project facts every check in this directory reads: `quality.json`.

The checks are generic — a complexity ratchet, a CRAP gate, a layering rule, test
hygiene ceilings, a document size ceiling, a capability-to-test map, mutation
testing — and everything that names a particular project lives in one JSON file
at the repository root, `quality.json`, with `quality.example.json` beside this
directory as the documented template. A check finds the file by walking up from
the working directory; `--config PATH` names one explicitly, which is how each
check's own test drives it over a throwaway tree.

Paths in the file are relative to the directory the file is in, and `path()`
resolves them. A key a check needs that the file lacks is an error naming the
key, not a default: the file is the whole of what the project says about
itself, and a silent default is a fact nobody decided.
"""
import json
import os
import sys

FILENAME = "quality.json"


def find(start=None):
    """The nearest quality.json at or above `start` (default: the working directory);
    None when there is none."""
    here = os.path.abspath(start or os.getcwd())
    while True:
        candidate = os.path.join(here, FILENAME)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


class Config:
    def __init__(self, path):
        self.file = os.path.abspath(path)
        self.root = os.path.dirname(self.file)
        with open(self.file) as handle:
            self.data = json.load(handle)

    def section(self, name):
        """The named top-level object; a check that needs it fails naming it when absent."""
        if name not in self.data:
            raise KeyError(f"{self.file} has no \"{name}\" section — see quality.example.json")
        return self.data[name]

    def get(self, section, key, default=None):
        value = self.section(section).get(key, default)
        if value is None:
            raise KeyError(f"{self.file}: \"{section}\" has no \"{key}\" — see quality.example.json")
        return value

    def entry(self, section, name):
        """One entry of a section that may be a list of named gates: the only one when
        `name` is None and there is one, else the one whose "name" matches — KeyError
        naming the choices otherwise."""
        raw = self.section(section)
        entries = raw if isinstance(raw, list) else [raw]
        if name is None and len(entries) == 1:
            return entries[0]
        for entry in entries:
            if entry.get("name") == name:
                return entry
        raise KeyError("%s: \"%s\" has no gate named %s (have: %s) — name one with --gate"
                       % (self.file, section, name, ", ".join(str(e.get("name")) for e in entries)))

    def path(self, relative):
        """A path from the file, resolved against the file's directory (absolute paths and
        `~` pass through)."""
        expanded = os.path.expanduser(relative)
        return expanded if os.path.isabs(expanded) else os.path.join(self.root, expanded)

    def paths(self, relatives):
        return [self.path(r) for r in relatives]


def load(explicit=None, start=None):
    """The Config to run under: `--config PATH` when given, else the nearest quality.json."""
    path = explicit or find(start)
    if path is None:
        print(f"FAIL: no {FILENAME} at or above {os.path.abspath(start or os.getcwd())} — "
              "copy quality.example.json to the repository root as quality.json and fill it in",
              file=sys.stderr)
        sys.exit(2)
    try:
        return Config(path)
    except (OSError, ValueError) as problem:
        print(f"FAIL: {path} could not be read: {problem}", file=sys.stderr)
        sys.exit(2)


def add_config_argument(parser):
    parser.add_argument("--config", help=f"the {FILENAME} to run under (default: the nearest one above the working directory)")

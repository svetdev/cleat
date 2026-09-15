"""languages — the one table of the languages cleat reads: each one's file suffixes and
the escapes worth a site each.

Every gate that takes a `languages` list in its section — escapes, complexity,
duplication, conventions, crap — and attach, which recognises a project's
languages by suffix, read this table. `language(name)` follows an alias
(`javascript` is `typescript`'s suffixes and patterns); `suffixes(names)` is the
file suffixes for a list of names, the shape a walker wants.
"""

# Per language: the file suffixes it lives in, and the escapes worth a site each.
# A pattern is a regex over the raw text — comments included, since most escapes
# are comments — anchored loosely enough to survive spacing.
LANGUAGES = {
    "python": {
        "suffixes": [".py"],
        "patterns": {
            "type ignore": r"#\s*type:\s*ignore",
            "noqa": r"#\s*noqa\b",
            "no cover": r"#\s*pragma:\s*no cover",
            "skipped test": r"pytest\.mark\.skip|pytest\.skip\(|unittest\.skip|@skip\b",
            "bare except": r"^\s*except\s*:",
        },
    },
    "typescript": {
        "suffixes": [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"],
        "patterns": {
            "any": r":\s*any\b|\bas\s+any\b|<any>",
            "ts-ignore": r"@ts-(?:ignore|expect-error|nocheck)",
            "eslint-disable": r"eslint-disable",
            "non-null assertion": r"[\w)\]]!\.",
            "skipped test": r"\b(?:it|test|describe)\.(?:skip|only)\(|\bx(?:it|test|describe)\(",
        },
    },
    "javascript": {"alias": "typescript"},
    "swift": {
        "suffixes": [".swift"],
        "patterns": {
            "force try": r"\btry!",
            "force cast": r"\bas!",
            "force unwrap": r"[\w)\]]!(?:\.|\s*[,;)\]]|$)",
            "swiftlint:disable": r"swiftlint:disable",
            "unchecked Sendable": r"@unchecked\s+Sendable",
            "skipped test": r"\bXCTSkip|\bthrow\s+XCTSkip",
        },
    },
    "rust": {
        "suffixes": [".rs"],
        "patterns": {
            "unwrap": r"\.unwrap\(\)",
            "expect": r"\.expect\(",
            "unsafe": r"\bunsafe\s*\{",
            "allow": r"#!?\[allow\(",
            "todo": r"\b(?:todo|unimplemented)!\(",
            "skipped test": r"#\[ignore\b",
        },
    },
    "kotlin": {
        "suffixes": [".kt", ".kts"],
        "patterns": {
            "not-null assertion": r"!!",
            "suppress": r"@Suppress\(",
            "skipped test": r"@(?:Ignore|Disabled)\b",
        },
    },
    "java": {
        "suffixes": [".java"],
        "patterns": {
            "suppress warnings": r"@SuppressWarnings\(",
            "skipped test": r"@(?:Ignore|Disabled)\b",
        },
    },
    "go": {
        "suffixes": [".go"],
        "patterns": {
            "nolint": r"//\s*nolint",
            "skipped test": r"\bt\.Skip(?:Now|f)?\(",
        },
    },
    "ruby": {
        "suffixes": [".rb"],
        "patterns": {
            "rubocop:disable": r"rubocop:disable",
            "skipped test": r"\bskip\b|\bxit\b|\bpending\b",
        },
    },
    "shell": {
        "suffixes": [".sh", ".bash", ".zsh"],
        "patterns": {
            "errors ignored": r"\|\|\s*true\b|^\s*set\s+\+e\b",
            "shellcheck disable": r"shellcheck\s+disable",
        },
    },
}


def language(name):
    """A language's suffixes and patterns, following an alias."""
    spec = LANGUAGES.get(name)
    if spec is None:
        raise KeyError("no built-in escape patterns for \"%s\" — one of: %s" % (name, ", ".join(sorted(LANGUAGES))))
    return LANGUAGES[spec["alias"]] if "alias" in spec else spec


def suffixes(names):
    """The file suffixes of the languages `names`, in one tuple."""
    return tuple(s for name in names for s in language(name)["suffixes"])


def every_suffix():
    """Every suffix any language in the table lives in."""
    return tuple(s for spec in LANGUAGES.values() if "suffixes" in spec for s in spec["suffixes"])

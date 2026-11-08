"""textsites — mutation sites found in a file's text, for a language with no parser in the replay.

The operators are mutsites.js's: a comparison, equality, `&&`/`||` (Python's and Ruby's
`and`/`or`), `+`/`-`, a boolean literal, a dropped prefix `!` (Python's `not`). Comments and string
literals are blanked first, offsets kept. A binary operator counts only with a space on each
side, as formatters write one, so a generic's `<T>`, an arrow, a shift, `+=` or a unary minus
is never one. A site the text misjudges makes a mutant that does not build; its tests then
report nothing and it is scored invalid, which costs a run and moves no score.

  sites(root, files) → [(file, offset, old, new)]
"""

import os
import re

HASH = (".py", ".rb", ".sh", ".bash", ".r", ".pl", ".ex", ".exs", ".jl", ".nim", ".cr", ".tf")
WORDS = (".py", ".rb")
TRUE_FALSE = {".py": ("True", "False")}

SWAP = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "==", "===": "!==", "!==": "===",
        "&&": "||", "||": "&&", "+": "-", "-": "+"}
BINARY = re.compile(r"(?<= )(===|!==|==|!=|<=|>=|&&|\|\||<|>|\+|-)(?= )")
WORD_BINARY = re.compile(r"(?<= )(and|or)(?= )")
NOT = re.compile(r"(?<![\w!=)\]])!(?=[A-Za-z_(])")   # not a macro's `name!(`, nor a postfix `x!`
WORD_NOT = re.compile(r"\bnot (?=[A-Za-z_(])")
STRING = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`(?:\\.|[^`\\])*`')
BLOCK = re.compile(r"/\*[\s\S]*?\*/")
LINE_SLASH = re.compile(r"//[^\n]*")
LINE_HASH = re.compile(r"#[^\n]*")


def blank(match):
    return re.sub(r"[^\n]", " ", match.group(0))


def code_only(text, suffix):
    """The text with comments and string literals turned to spaces, every offset kept."""
    out = STRING.sub(blank, text)
    if suffix in HASH:
        return LINE_HASH.sub(blank, out)
    return LINE_SLASH.sub(blank, BLOCK.sub(blank, out))


def in_file(path, text):
    suffix = os.path.splitext(path)[1].lower()
    code = code_only(text, suffix)
    out = [(path, m.start(), m.group(1), SWAP[m.group(1)]) for m in BINARY.finditer(code)]
    true, false = TRUE_FALSE.get(suffix, ("true", "false"))
    flip = {true: false, false: true}
    out += [(path, m.start(), m.group(0), flip[m.group(0)]) for m in re.finditer(r"\b(%s|%s)\b" % (true, false), code)]
    if suffix in WORDS:
        out += [(path, m.start(), m.group(1), "or" if m.group(1) == "and" else "and") for m in WORD_BINARY.finditer(code)]
        out += [(path, m.start(), "not ", "") for m in WORD_NOT.finditer(code)]
    if suffix != ".py":
        out += [(path, m.start(), "!", "") for m in NOT.finditer(code)]
    return out


def sites(root, files):
    out = []
    for path in files:
        with open(os.path.join(root, path), errors="replace") as handle:
            out += in_file(path, handle.read())
    return sorted(out)

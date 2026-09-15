"""typescript — where a TypeScript function's body really ends, when lizard runs past it.

lizard (1.24) does not close a `function` declaration that carries a plain return
type annotation — `function q(v?: string): string { … }` — at its closing brace.
The span runs on to whatever lizard next recognises, so a one-line function
followed by a block of `export interface` and `export type` declarations was
reported at 83 lines. Arrow functions, methods and generic return types
(`Promise<string>`) close where they should.

`body_end(lines, start, name)` reads only a named `function` declaration — the
one shape lizard misreads — and finds the parameter list after its name, walks the return type to the brace that opens the body
(a `{` after `:`, `|`, `&` or `,` is an object type, not the body), and matches
that brace, over the file with strings, template literals and comments blanked.
The reader keeps the earlier of lizard's end and this one, so the correction can
only shorten a span, never lengthen it.
"""

import re

SUFFIXES = (".ts", ".tsx", ".mts", ".cts")
NOT_CODE = re.compile(r"/\*[\s\S]*?\*/|//[^\n]*|'(?:\\.|[^'\\\n])*'?|\"(?:\\.|[^\"\\\n])*\"?|`(?:\\.|[^`\\])*`?")
PAIRS = {"(": ")", "[": "]", "{": "}", "<": ">"}
TYPE_BEFORE_BRACE = ":|&,"


def code_lines(text):
    """`text` with strings, template literals and comments blanked, newlines kept, as lines."""
    return NOT_CODE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text).split("\n")


def body_end(lines, start, name):
    """The 1-based line of the brace closing the body of the function lizard reports at
    `start` under `name`, or None when it cannot be found with confidence."""
    declared = declaration(lines, start, name)
    if declared is None:
        return None   # only a named `function` declaration is misread; an arrow or a method is left as lizard read it
    text = "\n".join(lines[start - 1:])
    closed = matching(text, text.find("(", declared))
    body = body_brace(text, closed + 1) if closed is not None else None
    close = matching(text, body) if body is not None else None
    return None if close is None else start + text.count("\n", 0, close)


def declaration(lines, start, name):
    """The column where `function name(` (or `function name<`) begins on the start line; None
    when that line declares no such function."""
    first = lines[start - 1] if name and 0 < start <= len(lines) else ""
    found = re.search(r"\bfunction\b[\s*]*%s\s*[<(]" % re.escape(name or ""), first)
    return found.start() if found else None


def matching(text, i):
    """The index of the bracket that closes the one at `i`; None when the text ends first."""
    if i < 0:
        return None
    opener = text[i]
    closer, depth = PAIRS[opener], 0
    for j in range(i, len(text)):
        if text[j] == opener:
            depth += 1
        elif text[j] == closer and not (closer == ">" and text[j - 1] == "="):
            depth -= 1
            if depth == 0:
                return j
    return None


def body_brace(text, i):
    """The index of the `{` opening the body, walking a return type from `i`; None for a
    signature with no body (an overload, an abstract member) or one this cannot read."""
    j = i
    while j < len(text):
        ch = text[j]
        if ch == ";":
            return None
        if ch == "{" and _previous(text, j) not in TYPE_BEFORE_BRACE:
            return j
        if ch in PAIRS:
            skipped = matching(text, j)
            if skipped is None:
                return None
            j = skipped
        j += 1
    return None


def _previous(text, j):
    """The last non-space character before `j`, or ""."""
    before = text[:j].rstrip()
    return before[-1] if before else ""

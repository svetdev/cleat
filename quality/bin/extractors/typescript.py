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


# ---------------------------------------------------------------- functions lizard does not report
#
# lizard (1.24) also drops whole TypeScript functions: after a `function` with a return type,
# or an object literal that spreads a call, the next declaration is read as part of the one
# before, or not at all. On five open-source libraries that was between a tenth and a third of
# their named functions, some of them over any ceiling. `declared` reads the three shapes that
# carry a name on their own line — a `function` declaration, a method, a `const` arrow — with
# the bracket matching above, and counts the decisions lizard counts. complexity.py adds the
# ones lizard did not report; what lizard did report stays as lizard read it.

NOT_A_NAME = frozenset("if for while switch catch return function super await new typeof throw else do with "
                       "import export delete void yield in of case constructor".split()) - {"constructor"}
MODIFIERS = r"(?:(?:export|default|declare|public|private|protected|static|async|override|abstract|get|set)\s+)*"
HEADER = re.compile(r"^[ \t]*%s(?:function\s*\*?\s*|\*\s*)?(?P<name>[#\w$]+)\s*(?:<[^(){};=]*>)?\(" % MODIFIERS, re.M)
ARROW = re.compile(r"^[ \t]*(?:export\s+)?(?:const|let|var)\s+(?P<name>[\w$]+)\s*(?::[^=\n]*)?=\s*(?:async\s*)?(?:<[^(){};=]*>\s*)?\(", re.M)
RETURN_TYPE = re.compile(r"\s*(?::[^\n;=]*(?:\n\s*[|&][^\n;=]*)*)?\s*$")
ARROW_TYPE = re.compile(r"\s*(?::[^\n;=]*(?:\n\s*[|&][^\n;=]*)*)?\s*=>\s*$")
DECISION = re.compile(r"\b(?:if|for|while|case|catch)\b|&&|\|\||\?(?![.?:,)\]=])")
GROUP = re.compile(r"\([^()]*\)|\[[^\[\]]*\]|\{[^{}]*\}|<[^<>]*>")


def _flat(text):
    """`text` with every bracketed group taken out, innermost first: what is left is the
    signature's own level, where a return type is one run and a statement shows."""
    previous = None
    while previous != text:
        previous, text = text, GROUP.sub("", text.replace("=>", "\x00"))
    return text.replace("\x00", "=>")


def _body(code, opened, between):
    """The index of the `{` opening the body of a declaration whose parameter list opens at
    `opened`; None when what follows the list is not a return type and a brace."""
    closed = matching(code, opened)
    brace = code.find("{", closed + 1) if closed is not None else -1
    while brace != -1 and not between.match(_flat(code[closed + 1:brace])):
        if "\n\n" in code[closed + 1:brace] or ";" in _flat(code[closed + 1:brace]):
            return None
        brace = code.find("{", brace + 1)
    return brace if brace != -1 else None


def declared(text):
    """[(name, first line, last line, cyclomatic number)] for the named functions of `text`,
    one per body: the declaration nearest a body is the one that owns it, so an overload
    signature above its implementation is not a second function."""
    code = "\n".join(code_lines(text))
    owners = {}
    for pattern, between in ((HEADER, RETURN_TYPE), (ARROW, ARROW_TYPE)):
        for found in pattern.finditer(code):
            if found.group("name") in NOT_A_NAME:
                continue
            body = _body(code, found.end() - 1, between)
            if body is not None:
                owners[body] = max(owners.get(body, found), found, key=lambda m: m.start())
    out = []
    for body, found in sorted(owners.items()):
        close = matching(code, body)
        if close is not None:
            out.append((found.group("name"), code.count("\n", 0, found.start("name")) + 1, code.count("\n", 0, close) + 1,
                        1 + len(DECISION.findall(code[body:close]))))
    return out

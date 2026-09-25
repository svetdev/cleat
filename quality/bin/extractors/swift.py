"""swift — the Swift lizard misreads, masked before it reads, and spans cut back after.

lizard (1.24) reads three things in a Swift file wrongly. A `self.init(` or
`super.init(` call — a convenience init delegating — is taken for an `init`
declaration: the call becomes a phantom function that swallows the ones after it,
and the real init runs to the end of its type. A regex literal (`/\\{[a-z]+/`)
has its brace counted as code. A `#if … #else … #endif` block has its branches
read one after the other, so an `if x {` opened in each is two braces opened and
one closed. On the pilot codebase a convenience init made most of a service file
one baselined function: a line added to any small method below it grew that
function and failed the gate.

`masked(text)` is what lizard reads instead, line for line: the `init` of every
`.init(` call renamed to `inix`, raw strings (`#"{"#`, which lizard does not know
either), regex literals and every `#if` branch but the first blanked. That fixes the cause — every function is read, none swallowed.

`body_end(lines, start, name)` finds the name lizard reports on its start line,
the first `{` after it outside parentheses (a default argument's closure is
inside them), and the brace that closes it, over the file with comments,
strings (raw and multi-line too), regex literals and every `#if` branch but the
first blanked. The reader keeps the earlier of lizard's end and this one, so the
correction can only shorten a span, never lengthen it.
"""

import re

SUFFIXES = (".swift",)
COMMENTS_AND_STRINGS = (
    r"/\*[\s\S]*?\*/|//[^\n]*"                                   # comments
    r"|(#+)\"\"\"[\s\S]*?\"\"\"\1|\"\"\"[\s\S]*?\"\"\""          # multi-line strings, raw or not
    r"|(#+)\"[^\n]*?\"\2|\"(?:\\.|[^\"\\\n])*\"?")               # one-line strings, raw or not
REGEX_LITERALS = (
    r"#/[^\n]*?/#"                                               # an extended regex literal
    r"|(?:(?<=[(\[,=:!&|?{};])|(?<=\breturn)|^)[ \t]*/(?![\s/*])(?:\\.|[^/\\\n])+/")   # a bare one
NOT_CODE = re.compile(COMMENTS_AND_STRINGS + "|" + REGEX_LITERALS, re.M)
COMMENTS_AND_STRINGS_RE = re.compile(COMMENTS_AND_STRINGS)
REGEX_LITERAL_RE = re.compile(REGEX_LITERALS, re.M)
DIRECTIVE_RE = re.compile(r"^\s*#(if|elseif|else|endif)\b")
CALLED_INIT_RE = re.compile(r"(?<=\.)init(?=\s*[(<])")
RAW_STRING_RE = re.compile(r"(#+)\"\"\"[\s\S]*?\"\"\"\1|(#+)\"[^\n]*?\"\2")


def masked(text):
    """`text` as lizard should read it, every line where it was: `.init(` calls renamed, raw
    strings and regex literals blanked, every `#if` branch but the first blanked."""
    text = RAW_STRING_RE.sub(_blank, CALLED_INIT_RE.sub("inix", text))
    return "\n".join(first_branches(_blank_regex_literals(text).split("\n")))


def _blank(match):
    return re.sub(r"[^\n]", " ", match.group(0))


def _blank_regex_literals(text):
    """Regex literals blanked, found over a copy with comments and strings blanked, so a
    slash in either is never read as one."""
    out = list(text)
    for match in REGEX_LITERAL_RE.finditer(COMMENTS_AND_STRINGS_RE.sub(_blank, text)):
        out[match.start():match.end()] = _blank(match)
    return "".join(out)


def code_lines(text):
    """`text` as lines, comments, strings and regex literals blanked and every `#if`
    branch but the first blanked too, newlines kept."""
    blanked = NOT_CODE.sub(_blank, text)
    return first_branches(blanked.split("\n"))


def first_branches(lines):
    """`lines` with the `#elseif` and `#else` branches of every `#if` blanked — lizard reads
    them all as one body; the first branch alone is the shape the compiler sees."""
    out, skipping = [], []   # per open `#if`: whether this branch is past its first
    for line in lines:
        directive = DIRECTIVE_RE.match(line)
        if directive:
            _enter(skipping, directive.group(1))
        out.append("" if directive or any(skipping) else line)
    return out


def _enter(skipping, kind):
    """The `#if` stack after the directive `kind`."""
    if kind == "if":
        skipping.append(False)
    elif skipping and kind == "endif":
        skipping.pop()
    elif skipping:
        skipping[-1] = True   # an #elseif or #else: past the first branch


def body_end(lines, start, name):
    """The 1-based line of the brace closing the body of the function lizard reports at
    `start` under `name`, or None when it cannot be found with confidence."""
    first = lines[start - 1] if name and 0 < start <= len(lines) else ""
    named = re.search(r"(?<![\w.])%s(?!\w)" % re.escape(name or ""), first)
    if not named:
        return None
    text = "\n".join(lines[start - 1:])
    opened = body_brace(text, named.end())
    closed = matching(text, opened) if opened is not None else None
    return None if closed is None else start + text.count("\n", 0, closed)


def body_brace(text, i):
    """The index of the first `{` from `i` outside parentheses and brackets; None for a
    declaration with no body (a protocol requirement) or one this cannot read."""
    depth = 0
    for j in range(i, len(text)):
        ch = text[j]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "{" and depth <= 0:
            return j
        elif depth <= 0 and _ends_declaration(text, j):
            return None
    return None


def _ends_declaration(text, j):
    """A `;`, or a newline before the next line opens another declaration — a body-less
    requirement ends there, and its `{` is not the next one in the file."""
    if text[j] == ";":
        return True
    return text[j] == "\n" and re.match(r"\n\s*(?:@\w+\s*)*(?:func|var|let|init|subscript|case)\b", text[j:]) is not None


def matching(text, i):
    """The index of the `}` that closes the `{` at `i`; None when the text ends first."""
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return None

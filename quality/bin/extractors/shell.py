"""shell — whether a Bash command line runs something with a flag, or only mentions it.

The guard refuses a command that writes a baseline. Matching the flag anywhere in
the command refused a heredoc that wrote a decision record quoting it, a commit
message that named it, a grep that searched for it. This splits a command where
the shell splits it — `;`, `&&`, `||`, `|`, a newline, a `$( )` or backtick
subshell — keeping quotes and heredocs whole, and asks each pipeline two things:
does any part of it carry the flag, and does any part of it run a program that is
not one of `PROSE_COMMANDS`, the commands that only carry or search text.

Both at once is a call. So `git commit -m "… --write-baseline …"` is a mention,
and so is a heredoc into `cat`; `echo --write-baseline | bash`, `python3 - <<EOF`
over a body that names it, and `bash -c "$(echo … --write-baseline)"` are calls:
a subshell's output and a heredoc's body belong to the command that consumes
them. An unquoted heredoc's body runs its own subshells, so those are judged too.

The direction of every doubt is refusal: a command word this does not know is a
program, not prose.

`writes` answers the guard's other question — which files a command writes: the
files handed to `sed -i`, `tee`, `cp`, `mv`, `rm`, `truncate` and `install`, and
the targets of `>` and `>>`, looking inside `sh -c` and `eval` strings and a
heredoc body's subshells. Only those words are paths; a sed script, a comment or
a heredoc body that names `quality.json` is text.

`Scan` is the reader underneath; attach uses it too, to tell a project's own hook
that runs `gate.py` from one that only names it.
"""

import os
import re
import shlex

# Commands that carry, print or search text and run nothing they are handed.
PROSE_COMMANDS = frozenset({"cat", "echo", "printf", "tee", "grep", "egrep", "fgrep", "rg", "ag", "ack",
                            "head", "tail", "less", "more", "wc", "jq", "gh", "git"})
HEREDOC_RE = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][\w.-]*)\2")
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_]\w*=")
# Words that stand in front of the program a simple command runs.
PREFIX_WORDS = frozenset({"then", "do", "else", "elif", "if", "while", "until", "exec", "env", "nohup", "time",
                          "command", "builtin"})
HEREDOC_TEXT = "heredoc"   # a frame's quote state for an unquoted heredoc's body: literal but for subshells
# Commands that write the files they are handed; the shells whose `-c` string is a command line of its own.
WRITERS = frozenset({"tee", "cp", "mv", "rm", "truncate", "install"})
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
WRAPPERS = PREFIX_WORDS | {"sudo", "xargs", "!", "(", ")", "{", "}"}
REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>", ">&"})
INPUTS = frozenset({"<", "<<", "<<<", "<>", "<&"})
SED_SCRIPT_OPTIONS = frozenset({"-e", "-f", "--expression", "--file"})
DIRECTORY_CHANGERS = frozenset({"cd", "pushd"})


def mentions_prefix(text, flag, shortest):
    """Whether `text` holds `flag` as a word, or an abbreviation of it argparse would
    expand (at least `shortest` characters): `--write-b` is `--write-baseline`."""
    words = re.findall(r"(?<![\w-])(%s[\w-]*)" % re.escape(shortest), text)
    return any(flag.startswith(word) for word in words)


def command_word(text):
    """The program a simple command runs: its first word past assignments, openers and the
    keywords that stand in front of one (`then`, `exec`, `env`), by basename; "" for none."""
    for word in re.split(r"[\s({]+", text):
        word = word.strip("'\"!")
        if word and not ASSIGNMENT_RE.match(word) and word not in PREFIX_WORDS:
            return os.path.basename(word)
    return ""


def words(text):
    """A simple command's words, each stripped of the quotes around it."""
    return [word.strip("'\"") for word in text.split()]


class Scan:
    """A command line as its simple commands: each a dict of its text (`chars`), its
    pipeline, the segment its output feeds (a subshell's `parent`), its `depth` (1 at the
    top), the operator it came `after` (`;`, `&&`, `||`, `|`, a newline, or "" for the
    first), and whether it is `literal` heredoc text."""

    def __init__(self, text, literal=False):
        self.text, self.segments, self.pipelines, self.pending = text, [], 0, []
        self.frames = []
        self._push(None, None, HEREDOC_TEXT if literal else None)
        index = 0
        while index < len(text):
            index = self._step(index)

    def _segment(self, frame, after=""):
        seg = {"chars": [], "pipeline": frame["pipeline"], "parent": frame["parent"], "depth": len(self.frames),
               "after": after, "literal": frame["quote"] == HEREDOC_TEXT and len(self.frames) == 1, "bodies": []}
        self.segments.append(seg)
        return seg

    def _push(self, closer, parent, quote=None):
        self.pipelines += 1
        frame = {"closer": closer, "quote": quote, "pipeline": self.pipelines, "parent": parent}
        self.frames.append(frame)
        frame["segment"] = self._segment(frame)

    def _put(self, chars):
        self.frames[-1]["segment"]["chars"].append(chars)

    def _step(self, i):
        text, frame = self.text, self.frames[-1]
        if frame["quote"] == "'":
            self._put(text[i])
            frame["quote"] = None if text[i] == "'" else "'"
            return i + 1
        if text[i] == "\\":
            self._put(text[i:i + 2])
            return i + 2
        opened = self._subshell(i)
        if opened:
            return opened
        if text[i] in "'\"":
            return self._quote(i)
        return self._operator(i) if frame["quote"] is None else self._literal(i)

    def _literal(self, i):
        self._put(self.text[i])
        return i + 1

    def _subshell(self, i):
        """The index past a `$(`, a backtick or a closing `)` this frame waits for; 0 for none."""
        return self._open_subshell(i) or self._close_subshell(i)

    def _open_subshell(self, i):
        text, frame = self.text, self.frames[-1]
        if not (text.startswith("$(", i) or (text[i] == "`" and frame["closer"] != "`")):
            return 0
        dollar = text[i] == "$"
        self._push(")" if dollar else "`", frame["segment"])
        return i + (2 if dollar else 1)

    def _close_subshell(self, i):
        frame = self.frames[-1]
        if len(self.frames) == 1 or frame["quote"] is not None or self.text[i] != frame["closer"]:
            return 0
        self.frames.pop()
        return i + 1

    def _quote(self, i):
        frame, ch = self.frames[-1], self.text[i]
        if frame["quote"] is None:
            frame["quote"] = ch
        elif frame["quote"] == ch:
            frame["quote"] = None
        self._put(ch)
        return i + 1

    def _operator(self, i):
        text = self.text
        heredoc = HEREDOC_RE.match(text, i) if text.startswith("<<", i) and not text.startswith("<<<", i) else None
        if heredoc:
            self.pending.append((heredoc, self.frames[-1]["segment"]))
            self._put(heredoc.group(0))
            return heredoc.end()
        if text[i] == "\n":
            self._split(pipe=False, after="\n")
            return self._bodies(i + 1)
        if text.startswith("||", i) or text.startswith("&&", i):
            self._split(pipe=False, after=text[i:i + 2])
            return i + 2
        if text[i] in "|;&":
            self._split(pipe=text[i] == "|", after=text[i])
            return i + 1
        return self._literal(i)

    def _split(self, pipe, after):
        frame = self.frames[-1]
        if not pipe:
            self.pipelines += 1
            frame["pipeline"] = self.pipelines
        frame["segment"] = self._segment(frame, after)

    def _bodies(self, i):
        """Past the bodies of the heredocs the line just ended declared, each handed to
        the segment that opened it."""
        for heredoc, seg in self.pending:
            seg.setdefault("own", "".join(seg["chars"]))   # the command's own words, before any body
            dash, quoted, delimiter = heredoc.group(1), heredoc.group(2), heredoc.group(3)
            body, i = _heredoc_body(self.text, i, delimiter, bool(dash))
            seg["chars"].append("\n" + body)
            if not quoted:
                seg["bodies"].append(Scan(body, literal=True))
        self.pending = []
        return i


def _heredoc_body(text, i, delimiter, dash):
    """(the body starting at `i`, the index past its delimiter line)."""
    lines, end = [], i
    while end < len(text):
        stop = text.find("\n", end)
        stop = len(text) if stop < 0 else stop
        line = text[end:stop]
        if (line.lstrip("\t") if dash else line) == delimiter:
            return "\n".join(lines), stop + 1
        lines.append(line)
        end = stop + 1
    return "\n".join(lines), len(text)


def _prose(seg):
    return seg["literal"] or command_word("".join(seg["chars"])) in PROSE_COMMANDS | {""}


def _carries(seg, flag, shortest):
    return mentions_prefix("".join(seg["chars"]), flag, shortest)


def runs_with_flag(command, flag, shortest):
    """Whether `command` runs a program with `flag` (or an abbreviation of it), rather
    than only quoting, printing or searching for it."""
    return _runs(Scan(command), flag, shortest)


def _runs(scan, flag, shortest):
    if any(_runs(body, flag, shortest) for seg in scan.segments for body in seg["bodies"]):
        return True
    carried = _carried(scan, flag, shortest)
    return any(carries and runs for carries, runs in _pipelines(scan, carried).values())


def _carried(scan, flag, shortest):
    """The ids of the segments that carry the flag — in their own text, or through a
    subshell whose output they consume."""
    carried = {id(seg) for seg in scan.segments if _carries(seg, flag, shortest)}
    for seg in sorted(scan.segments, key=lambda s: -s["depth"]):
        if id(seg) in carried and seg["parent"] is not None:
            carried.add(id(seg["parent"]))
    return carried


def _pipelines(scan, carried):
    """{pipeline: (whether a part carries the flag, whether a part runs a program)}."""
    pipelines = {}
    for seg in scan.segments:
        carries, runs = pipelines.get(seg["pipeline"], (False, False))
        pipelines[seg["pipeline"]] = (carries or id(seg) in carried, runs or not _prose(seg))
    return pipelines


def own_text(seg):
    """A segment's own command line, without the heredoc bodies it was handed."""
    return seg.get("own", "".join(seg["chars"]))


def tokens(text):
    """A simple command's words as the shell splits them: quotes removed, a comment
    dropped, `>` and its kin words of their own. A line shlex cannot read (an unclosed
    quote) falls back to whitespace."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return words(text)


def writes(command):
    """The words `command` writes to as files — see the module's docstring."""
    return _writes(Scan(command))


def changes_directory(command):
    """Whether `command` moves with `cd` or `pushd`, after which a relative path is not
    relative to the directory the command started in."""
    return any(command_word(own_text(seg)) in DIRECTORY_CHANGERS for seg in Scan(command).segments)


def _writes(scan):
    found = [path for seg in scan.segments for body in seg["bodies"] for path in _writes(body)]
    for seg in scan.segments:
        if not seg["literal"]:
            found += _segment_writes(own_text(seg))
    return found


def _segment_writes(text):
    rest, targets = _redirects(tokens(text))
    program, args = _program(rest)
    return targets + _written_by(program, args)


def _redirects(toks):
    """(the words that are not redirections, the targets of the output ones)."""
    rest, targets, i = [], [], 0
    while i < len(toks):
        step = _redirection(toks, i)
        if step:
            targets += step[1]
            i += step[0]
        else:
            rest.append(toks[i])
            i += 1
    return rest, targets


def _redirection(toks, i):
    """(words it spans, [its target]) for a redirection starting at `i`; None for a word."""
    tok, following = toks[i], toks[i + 1:i + 2]
    if tok.isdigit() and following and following[0] in REDIRECTS | INPUTS:
        return 1, []                                # the descriptor in front of `2>`
    if tok not in REDIRECTS | INPUTS:
        return None
    written = tok in REDIRECTS and following and not re.fullmatch(r"\d+|-", following[0])
    return 2, (following if written else [])


def _program(toks):
    """(the program a simple command runs by basename, its arguments), past assignments
    and the words that stand in front of one."""
    for i, tok in enumerate(toks):
        if tok and tok not in WRAPPERS and not ASSIGNMENT_RE.match(tok):
            return os.path.basename(tok), toks[i + 1:]
    return "", []


def _written_by(program, args):
    if program in SHELLS:
        return _shell_string_writes(args)
    if program == "eval":
        return writes(" ".join(args))
    if program == "sed":
        return _sed_files(args)
    return _positionals(args) if program in WRITERS else []


def _positionals(args):
    """The arguments that are not options (all of them after `--`)."""
    found, options = [], True
    for arg in args:
        if options and arg == "--":
            options = False
        elif arg and not (options and arg.startswith("-") and arg != "-"):
            found.append(arg)
    return found


def _sed_files(args):
    """The files `sed -i` edits in place: its operands, less the script when no `-e` or
    `-f` gave one. Without -i sed writes nothing."""
    if not any(re.match(r"-[a-zA-Z]*i|--in-place", arg) for arg in args):
        return []
    operands, scripted = _sed_operands(args)
    return operands if scripted else operands[1:]


def _sed_operands(args):
    """(sed's operands, whether an option gave the script) — the word after -e or -f is
    the script, not an operand."""
    operands, scripted, skip = [], False, False
    for arg in args:
        if skip:
            skip = False
            continue
        scripted = scripted or arg in SED_SCRIPT_OPTIONS or arg.startswith(("--expression=", "--file="))
        skip = arg in SED_SCRIPT_OPTIONS
        if arg and not arg.startswith("-"):
            operands.append(arg)
    return operands, scripted


def _shell_string_writes(args):
    """What `sh -c STRING` writes: the string is a command line of its own."""
    for i, arg in enumerate(args[:-1]):
        if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", arg):
            return writes(args[i + 1])
    return []

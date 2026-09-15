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

`Scan` is the reader underneath; attach uses it too, to tell a project's own hook
that runs `gate.py` from one that only names it.
"""

import os
import re

# Commands that carry, print or search text and run nothing they are handed.
PROSE_COMMANDS = frozenset({"cat", "echo", "printf", "tee", "grep", "egrep", "fgrep", "rg", "ag", "ack",
                            "head", "tail", "less", "more", "wc", "jq", "gh", "git"})
HEREDOC_RE = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][\w.-]*)\2")
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_]\w*=")
# Words that stand in front of the program a simple command runs.
PREFIX_WORDS = frozenset({"then", "do", "else", "elif", "if", "while", "until", "exec", "env", "nohup", "time",
                          "command", "builtin"})
HEREDOC_TEXT = "heredoc"   # a frame's quote state for an unquoted heredoc's body: literal but for subshells


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

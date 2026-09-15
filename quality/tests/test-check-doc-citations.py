#!/usr/bin/env python3
"""test-check-doc-citations — assert quality/bin/check-doc-citations.py.

A document citing files that exist passes counting them; one citing a moved
file fails naming the document, line and path; a backticked span that is not a
path (a word, a command with spaces, a suffix not in the list) is not read; a
path with a line suffix resolves; --root chooses where; the config's list is
read and a missing document is refused; a sentence saying the file is to be added or
is gone excuses its citation, and a cue inside backticks does not. Writes nothing outside a temporary directory.

  python3 quality/tests/test-check-doc-citations.py
"""
import json, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "check-doc-citations.py")
suite = Suite("test-check-doc-citations"); check = suite.check



def run(*args):
    proc = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, cwd=tmp)
    return proc.returncode, proc.stdout + proc.stderr


tmp = tempfile.mkdtemp(prefix="doc-citations-")
try:
    write(os.path.join(tmp, "src", "store.py"), "x = 1\n")
    write(os.path.join(tmp, "src", "views", "page.ts"), "export {}\n")
    doc = os.path.join(tmp, "docs", "arch.md")
    write(doc, "The store is `src/store.py` and the page `src/views/page.ts:12`.\nRun `python3 tool.py --all` or `make`; see `README.md`.\nA `.env` is not cited; nor `foo.bar`.\n")
    write(os.path.join(tmp, "README.md"), "hi\n")
    code, out = run("--file", doc, "--root", tmp)
    check("a document whose citations resolve passes, counting them", code == 0 and "all 3 cited path(s) resolve" in out, out)
    write(os.path.join(tmp, ".worktrees", "feature", "src", "data", "store.py"), "x = 9\n")
    write(os.path.join(tmp, ".worktrees", "feature", ".git"), "gitdir: elsewhere\n")
    write(doc, "See `store.py`.\n")
    code, out = run("--file", doc, "--root", tmp)
    check("a copy inside a nested checkout (a worktree under the tree) does not make a bare name ambiguous", code == 0, out)
    write(doc, "The store is `src/data/store.py` and the page `src/views/page.ts`.\n")
    code, out = run("--file", doc, "--root", tmp)
    check("a moved file fails naming the document, line and path", code == 1 and ":1  `src/data/store.py`" in out and "1 path(s) that resolve nowhere" in out, out)
    check("and the fix says what to do", "Point the citation" in out, out)
    code, out = run("--file", doc, "--root", tmp, "--root", os.path.join(tmp, "src"))
    check("a second --root resolves the same path from elsewhere", code == 1, out)
    write(doc, "See `page.ts` and `store.py` and `nowhere.py`.\n")
    code, out = run("--file", doc, "--root", tmp)
    check("a bare filename resolves when exactly one file under the roots has that name", "`page.ts`" not in out, out)
    check("one nobody has is missing, saying so", code == 1 and "`nowhere.py` — no file of that name" in out, out)
    write(os.path.join(tmp, "lib", "store.py"), "x = 3\n")
    code, out = run("--file", doc, "--root", tmp)
    check("two candidates is ambiguity, reported with both", "`store.py` — ambiguous" in out and "lib/store.py" in out and "src/store.py" in out, out)
    os.remove(os.path.join(tmp, "lib", "store.py"))
    write(os.path.join(tmp, ".worktrees", "feature", "src", "data", "store.py"), "x = 9\n")
    write(os.path.join(tmp, ".worktrees", "feature", ".git"), "gitdir: elsewhere\n")
    write(doc, "See `store.py`.\n")
    code, out = run("--file", doc, "--root", tmp)
    check("a copy inside a nested checkout (a worktree under the tree) does not make a bare name ambiguous", code == 0, out)
    write(doc, "The store is `src/data/store.py` and the page `src/views/page.ts`.\n")
    write(os.path.join(tmp, "src", "data", "store.py"), "x = 2\n")
    code, out = run("--file", doc, "--root", tmp, "--quiet")
    check("--quiet prints nothing on success", code == 0 and out == "", repr(out))
    # a sentence that says the file is to be created, or is gone, cites it on purpose
    write(doc, "Add `e2e/stack/new-flow.spec.ts` for the booking flow.\nThe retired `bin/old.py` is gone. See `src/store.py`.\n"
               "The `bin/gone.py` script handles exports.\n")
    code, out = run("--file", doc, "--root", tmp)
    check("a citation whose sentence says the file is to be added or is gone is excused", "new-flow.spec.ts" not in out and "old.py" not in out, out)
    check("but only in its own sentence: the next sentence on the line is judged alone, and resolves", "store.py" not in out, out)
    check("a cue-less sentence citing a missing file still fails", code == 1 and "`bin/gone.py` — not under the roots" in out, out)
    write(doc, "Add `e2e/stack/new-flow.spec.ts` for the booking flow. See `src/store.py`.\n")
    code, out = run("--file", doc, "--root", tmp)
    check("the success line counts the excused citations apart", code == 0 and "all 1 cited path(s) resolve (1 named as to be created or gone)" in out, out)
    write(doc, "The script `bin/add.py` handles exports.\n")
    code, out = run("--file", doc, "--root", tmp)
    check("a cue word inside backticks is not a cue", code == 1 and "`bin/add.py`" in out, out)
    write(doc, "The store is `src/data/store.py` and the page `src/views/page.ts`.\n")
    config = os.path.join(tmp, "quality.json")
    write(config, json.dumps({"doc_citations": [{"file": "docs/arch.md", "roots": ["."]}, {"file": "README.md", "roots": ["src"], "extensions": [".py"]}]}))
    code, out = run("--config", config)
    check("the config's list judges each document against its roots", code == 0 and "OK: docs/arch.md" in out and "OK: README.md — all 0" in out, out)
    write(config, json.dumps({"doc_citations": [{"file": "docs/gone.md", "roots": ["."]}]}))
    code, out = run("--config", config)
    check("a missing document is refused", code == 2 and "docs/gone.md" in out, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

#!/usr/bin/env python3
"""test-check-public-api — assert quality/bin/check-public-api.py and extractors/surface.py.

Over throwaway TypeScript, Python, Rust, Go and Swift trees: the built-in
reader lists what each language marks as exported and nothing else (a private
function, a `pub(crate)` item, a lowercase Go name, a Python underscore name
or one outside `__all__`); --write-baseline records the surface; the same
tree passes; a removed export fails as a break naming it; a renamed parameter
fails the same way; an added export is a NOTE with the recording command and
a --strict failure; the cargo-public-api and api-extractor reports are read;
an unknown language or report kind is refused. Python is read from its syntax
tree: a parameter list over several lines is one signature however it is laid
out, an exported class's methods count, a private module exports only what a
public one imports from it, a signature that changes only in a parameter's
annotation or gains what no caller has to pass is a NOTE that --tighten records, and a baseline the line reader wrote
is still judged by lines. Writes nothing outside a temporary directory.

  python3 quality/tests/test-check-public-api.py
"""
import json, os, shutil, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write, run as run_script
BIN = os.path.join(os.path.dirname(HERE), "bin")
sys.path.insert(0, BIN)
from extractors import pysurface, surface
SCRIPT = os.path.join(BIN, "check-public-api.py")
suite = Suite("test-check-public-api"); check = suite.check


def run(config, *args):
    return run_script(SCRIPT, "--config", config, *args)


tmp = tempfile.mkdtemp(prefix="public-api-")
try:
    ts = os.path.join(tmp, "sdk", "src")
    write(os.path.join(ts, "index.ts"), "export class Client {\n  run() {}\n}\nexport function connect(url: string, retries = 3): Client { return new Client() }\nexport const VERSION = '1'\nexport type Options = { a: number }\nfunction internal() {}\nexport { internal as helper }\n")
    sigs = surface.built_in([ts], "typescript", tmp)
    check("typescript: exported declarations are read as signatures, a private function is not",
          [s for _, _, s in sigs] == ["export class Client", "export function connect(url: string, retries = 3): Client", "export const VERSION", "export type Options"], str(sigs))
    py = os.path.join(tmp, "pkg")
    write(os.path.join(py, "api.py"), "__all__ = ['run', 'Client']\n\nclass Client:\n    pass\n\ndef run(a, b=1):\n    pass\n\ndef _private():\n    pass\n\ndef not_listed():\n    pass\n")
    write(os.path.join(py, "free.py"), "def go(x):\n    pass\n\ndef _hidden():\n    pass\n\nLIMIT = 3\n")
    sigs = sorted(s for _, _, s in surface.built_in([py], "python", tmp))
    check("python: __all__ restricts, an underscore hides, a constant counts", sigs == ["LIMIT", "class Client", "def go(x)", "def run(a, b=1)"], str(sigs))
    rs = os.path.join(tmp, "crate")
    write(os.path.join(rs, "lib.rs"), "pub struct Store;\npub(crate) fn hidden() {}\npub fn open(path: &str) -> Store { Store }\nfn private() {}\npub enum Kind { A }\n")
    sigs = [s for _, _, s in surface.built_in([rs], "rust", tmp)]
    check("rust: pub items count, pub(crate) and private do not", sigs == ["pub struct Store", "pub fn open(path: &str) -> Store", "pub enum Kind"], str(sigs))
    go = os.path.join(tmp, "gopkg")
    write(os.path.join(go, "store.go"), "package store\n\ntype Store struct{}\nfunc New(path string) *Store { return nil }\nfunc (s *Store) Run() {}\nfunc helper() {}\ntype thing int\n")
    sigs = [s for _, _, s in surface.built_in([go], "go", tmp)]
    check("go: a capital letter exports, a lowercase name does not", sigs == ["type Store struct", "func New(path string) *Store", "func (s *Store) Run()"], str(sigs))
    sw = os.path.join(tmp, "swiftpkg")
    write(os.path.join(sw, "Store.swift"), "public struct Store {\n    public init(path: String) {}\n    public func run() {}\n    func hidden() {}\n}\nopen class Base {}\n")
    sigs = [s for _, _, s in surface.built_in([sw], "swift", tmp)]
    check("swift: public and open count, internal does not", sigs == ["public struct Store", "public init(path: String)", "public func run()", "open class Base"], str(sigs))

    config = os.path.join(tmp, "quality.json")
    write(config, json.dumps({"public_api": [{"name": "sdk", "language": "typescript", "roots": ["sdk/src"], "baseline": "api-sdk.json"}]}))
    code, out = run(config, "--gate", "sdk")
    check("with no baseline every signature is an unrecorded addition: a NOTE, not a failure", code == 0 and "4 public signature(s) of sdk not yet recorded" in out and "--write-baseline" in out, out)
    code, out = run(config, "--gate", "sdk", "--strict")
    check("and under --strict a failure", code == 1, out)
    code, out = run(config, "--gate", "sdk", "--write-baseline")
    check("--write-baseline records the surface", code == 0 and "4 public signature(s) of sdk recorded" in out, out)
    code, out = run(config, "--gate", "sdk", "--strict")
    check("the same tree passes, naming the count", code == 0 and "all 4 recorded public signature(s) still there" in out, out)
    code, out = run(config, "--gate", "sdk", "--quiet")
    check("--quiet prints nothing on success", out == "", repr(out))

    write(os.path.join(ts, "index.ts"), "export class Client {\n  run() {}\n}\nexport function connect(url: string, attempts = 3): Client { return new Client() }\nexport type Options = { a: number }\n")
    code, out = run(config, "--gate", "sdk")
    check("a removed export and a renamed parameter fail as a break, naming both old signatures", code == 1 and "2 public signature(s) of sdk recorded in the baseline are gone" in out and "export const VERSION" in out and "retries = 3" in out, out)
    check("the new signature is listed as unrecorded, without the recording command beside the break", "attempts = 3" in out and "Record them" not in out, out)
    check("the fix names the deprecated-alias route", "deprecated alias" in out, out)

    report_path = os.path.join(tmp, "public-api.txt")
    write(report_path, "pub fn core::open(path: &str) -> core::Store\npub struct core::Store\n\n")
    api_md = os.path.join(tmp, "sdk.api.md")
    write(api_md, "## API Report\n\n```ts\n\n// @public\nexport class Client {\n    run(): void;\n}\n\n// @public\nexport function connect(url: string): Client;\n\n```\n")
    write(config, json.dumps({"public_api": [
        {"name": "core", "report": {"cargo-public-api": "public-api.txt"}, "baseline": "api-core.json"},
        {"name": "web", "report": {"api-extractor": "sdk.api.md"}, "baseline": "api-web.json"},
        {"name": "bad", "report": {"japicmp": "x"}, "baseline": "b.json"},
        {"name": "cobol", "language": "cobol", "baseline": "c.json"}]}))
    code, out = run(config, "--gate", "core", "--write-baseline")
    check("a cargo-public-api report is read, one item per line", code == 0 and "2 public signature(s) of core recorded" in out, out)
    code, out = run(config, "--gate", "web", "--write-baseline")
    check("an api-extractor report is read from its ts fences", code == 0 and "2 public signature(s) of web recorded" in out, out)
    write(report_path, "pub struct core::Store\n")
    code, out = run(config, "--gate", "core")
    check("an item gone from the report fails as a break", code == 1 and "pub fn core::open(path: &str) -> core::Store" in out, out)
    code, out = run(config, "--gate", "bad")
    check("an unknown report kind is refused naming the known ones", code == 2 and "japicmp" in out and "cargo-public-api" in out, out)
    code, out = run(config, "--gate", "cobol")
    check("an unknown language is refused naming the known ones", code == 2 and "cobol" in out and "typescript" in out, out)
    code, out = run(config, "--gate", "nope")
    check("an unknown gate name is refused", code == 2 and "core, web" in out, out)

    lib = os.path.join(tmp, "lib")
    one_line = "def send(url, retries=3, *, timeout=None):\n    pass\n"
    spread = "def send(\n    url,  # where\n    retries=3,\n    *,\n    timeout=None,\n):\n    pass\n"
    klass = ("class Client(Base):\n    def __init__(self, url):\n        pass\n\n    def run(self, job: str) -> int:\n        pass\n\n"
             "    async def close(self):\n        pass\n\n    def _hidden(self):\n        pass\n\nclass _Private:\n    def run(self):\n        pass\n")
    write(os.path.join(lib, "net.py"), spread + klass)
    sigs = [s for _, _, s in surface.built_in([lib], "python", tmp)]
    check("python: a parameter list spread over lines is read whole, comments and trailing comma aside",
          sigs[0] == "def send(url, retries=3, *, timeout=None)", str(sigs))
    check("python: an exported class's methods and __init__ count, an underscore method and a private class's do not",
          sigs[1:] == ["class Client(Base)", "def Client.__init__(self, url)", "def Client.run(self, job: str) -> int", "async def Client.close(self)"], str(sigs))
    write(os.path.join(lib, "net.py"), one_line + klass)
    check("python: the same list on one line is the same signature", [s for _, _, s in surface.built_in([lib], "python", tmp)] == sigs)
    by_line = [s for _, _, s in surface.built_in([lib], "python", tmp, by_line=True)]
    check("python: the line reader still reads one declaration line each and no method", by_line == ["def send(url, retries=3, *, timeout=None)", "class Client(Base)"], str(by_line))

    write(os.path.join(lib, "_make.py"), "def build(x):\n    pass\n\ndef helper(x):\n    pass\n\nclass Thing:\n    def go(self):\n        pass\n")
    write(os.path.join(lib, "_vendor", "six.py"), "def moved(x):\n    pass\n")
    write(os.path.join(lib, "__init__.py"), "from ._make import build, Thing as Item\n")
    sigs = sorted(s for f, _, s in surface.built_in([lib], "python", tmp) if "net.py" not in f)
    check("python: a private module exports only the names a public module imports from it, a private directory nothing",
          sigs == ["class Thing", "def Thing.go(self)", "def build(x)"], str(sigs))
    write(os.path.join(lib, "broken.py"), "def ok(a):\n    pass\n\ndef (:\n")
    sigs = [s for f, _, s in surface.built_in([lib], "python", tmp) if "broken" in f]
    check("python: a file that does not parse is read by lines", sigs == ["def ok(a)"], str(sigs))
    os.remove(os.path.join(lib, "broken.py"))

    wide = pysurface.widened
    check("a parameter with a default, *args, **kwargs or a keyword-only default only widens a signature",
          wide("def f(a)", "def f(a, b=1)") and wide("def f(a)", "def f(a, *args, **kw)") and wide("def C.f(self, a, *, k=1)", "def C.f(self, a, *, k=1, j=None)"))
    check("a required parameter, a renamed one, a changed default, a changed return or another name does not",
          not any(wide(old, new) for old, new in [("def f(a)", "def f(a, b)"), ("def f(a)", "def f(b, c=1)"), ("def f(a=1)", "def f(a=2)"),
                                                   ("def f(a) -> int", "def f(a, b=1) -> str"), ("def f(a)", "def g(a, b=1)"), ("def f(a)", "def f(a)")]))
    check("nor a new named parameter where **kwargs took that argument before, or a positional one behind *args",
          not wide("def f(a, **kw)", "def f(a, b=1, **kw)") and not wide("def f(a, *rest)", "def f(a, b=1, *rest)") and not wide("def f(**kw)", "def f(*, b=1, **kw)"))
    check("a parameter's annotation may change, what the function returns may not",
          wide("def f(a: int, *, k: str=None) -> int", "def f(a: int | str, *, k: bytes=None) -> int") and not wide("def f(a: int) -> int", "def f(a: int) -> str"))
    check("nor anything that is not a Python function", not wide("export function f(a)", "export function f(a, b = 1)") and not wide("class A", "class A(B)"))

    write(config, json.dumps({"public_api": [{"name": "lib", "language": "python", "roots": ["lib"], "baseline": "api-lib.json"}]}))
    code, out = run(config, "--gate", "lib", "--write-baseline")
    recorded = json.load(open(os.path.join(tmp, "api-lib.json")))
    check("a Python baseline records which reader wrote it", code == 0 and recorded["provenance"]["version"] == pysurface.READER, str(recorded["provenance"]))
    write(os.path.join(lib, "net.py"), spread + klass)
    code, out = run(config, "--gate", "lib", "--strict")
    check("reflowing a signature over several lines is no change at all", code == 0, out)
    write(os.path.join(lib, "net.py"), spread.replace("timeout=None,", "timeout=None,\n    proxy=None,") + klass.replace("job: str)", "job: str, *extra)"))
    code, out = run(config, "--gate", "lib")
    check("an added keyword with a default and an added *args are a NOTE naming old and new, not a break",
          code == 0 and "2 public signature(s) of lib changed only in a parameter's annotation or in what no caller has to pass" in out and "timeout=None)  ->  def send(url, retries=3, *, timeout=None, proxy=None)" in out
          and "check-public-api.py --gate lib --tighten" in out and "are gone" not in out, out)
    code, out = run(config, "--gate", "lib", "--strict")
    check("which --strict fails until it is recorded", code == 1 and "must match exactly" in out, out)
    code, out = run(config, "--gate", "lib", "--tighten")
    check("--tighten records the wider signatures and passes", code == 0 and "2 compatible signature change(s) of lib recorded" in out, out)
    code, out = run(config, "--gate", "lib", "--strict")
    check("after which the tree matches exactly", code == 0 and "still there" in out, out)
    write(os.path.join(lib, "net.py"), spread + klass)
    code, out = run(config, "--gate", "lib", "--tighten")
    check("and narrowing them again is a break that --tighten does not record",
          code == 1 and "2 public signature(s) of lib recorded in the baseline are gone" in out and "proxy=None" in out, out)
    write(os.path.join(lib, "net.py"), spread.replace("url,  # where", "url,\n    proxy,") + klass.replace("def run(self, job: str)", "def run(self, task: str)"))
    code, out = run(config, "--gate", "lib")
    check("a required parameter in a spread list and a renamed method parameter are breaks", code == 1 and "2 public signature(s) of lib recorded in the baseline are gone" in out
          and "def Client.run(self, job: str, *extra) -> int" in out, out)

    write(os.path.join(lib, "net.py"), spread + klass)
    old = [dict(file=f, text=s, line=n) for f, n, s in surface.built_in([lib], "python", tmp, by_line=True)]
    write(os.path.join(tmp, "api-lib.json"), json.dumps({"provenance": {"tool": "public-api", "version": None, "config": recorded["provenance"]["config"]}, "entries": old}))
    check("the line reader had recorded a spread signature as its name alone, and a private module's names",
          {"def send(", "def helper(x)", "def moved(x)"} <= {e["text"] for e in old}, str(old))
    code, out = run(config, "--gate", "lib", "--strict")
    check("a baseline the line reader wrote is judged by lines: no method reads as an addition, no private name as gone",
          code == 0 and "recorded by an earlier reader" in out and "not yet recorded" not in out and "are gone" not in out, out)
    write(os.path.join(lib, "_make.py"), "def build(x):\n    pass\n\nclass Thing:\n    def go(self):\n        pass\n")
    code, out = run(config, "--gate", "lib")
    check("and what it recorded is still held", code == 1 and "def helper(x)" in out, out)
    code, out = run(config, "--gate", "lib", "--write-baseline")
    code, out = run(config, "--gate", "lib", "--strict")
    check("until --write-baseline records the surface as it is read now", code == 0 and "earlier reader" not in out, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

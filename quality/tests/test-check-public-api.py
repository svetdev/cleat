#!/usr/bin/env python3
"""test-check-public-api — assert quality/bin/check-public-api.py and extractors/surface.py.

Over throwaway TypeScript, Python, Rust, Go and Swift trees: the built-in
reader lists what each language marks as exported and nothing else (a private
function, a `pub(crate)` item, a lowercase Go name, a Python underscore name
or one outside `__all__`); --write-baseline records the surface; the same
tree passes; a removed export fails as a break naming it; a renamed parameter
fails the same way; an added export is a NOTE with the recording command and
a --strict failure; the cargo-public-api and api-extractor reports are read;
an unknown language or report kind is refused. Writes nothing outside a
temporary directory.

  python3 quality/tests/test-check-public-api.py
"""
import json, os, shutil, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Suite, write, run as run_script
BIN = os.path.join(os.path.dirname(HERE), "bin")
sys.path.insert(0, BIN)
from extractors import surface
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
finally:
    shutil.rmtree(tmp, ignore_errors=True)

suite.finish()

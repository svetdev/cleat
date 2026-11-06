"""cargo — the Rust side of the paired runs: which files are production, how a task's
dependencies are fetched before the agent starts, and how its tests are run offline.

One CARGO_HOME is shared by every task (`.study-work/rust-cargo-home`): fetched into
at prep time, when the network is allowed, and only read afterwards — the agent's
sandbox can read it but not write it, and cargo builds offline against it. Each repo
has one shared target dir for validation (`.study-work/rust-target/<repo>`); a task
tree starts from an APFS clone of it, so the dependencies are already built and only
the workspace's own crates compile — a dependency's artifacts are keyed by its path
in the shared CARGO_HOME, which every tree sees at the same place.
"""

import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAIRED = os.path.dirname(HERE)
STUDY = os.path.dirname(PAIRED)
CLEAT = os.path.dirname(STUDY)
WORK_ROOT = os.path.join(CLEAT, ".study-work")
CARGO_HOME = os.environ.get("RUST_CARGO_HOME", os.path.join(WORK_ROOT, "rust-cargo-home"))
TARGETS = os.environ.get("RUST_TARGETS", os.path.join(WORK_ROOT, "rust-target"))
sys.path.insert(0, os.path.join(STUDY, "jit"))
sys.path.insert(0, PAIRED)
import mine    # noqa: E402  the JIT study's commit reader; it puts quality/bin on the path
import summary  # noqa: E402  cleat's test/aside rule
from extractors import languages  # noqa: E402

TIMEOUT = 1500          # one build-and-test step during validation
TEST_LINE = re.compile(r"^test (.+?) \.\.\. (ok|FAILED|ignored)\b", re.M)
CFG_TEST = re.compile(r"^\s*#\[cfg\(test\)\]\s*$")
ESCAPES = [re.compile(p, re.M) for p in languages.LANGUAGES["rust"]["patterns"].values()]
MANIFESTS = ("Cargo.toml", "Cargo.lock")
ALL = "*"                # a target's features: every feature of its package


def production(path):
    """cleat's rule, as mine.production applies it to Python: a .rs file that is not a test,
    an example, a benchmark or a doc. `src/x/tests.rs` is production by that rule."""
    return path.endswith(".rs") and not summary._is_test(path)


def is_test(path):
    """A test file the hidden tests can run: an integration-test file, by cleat's rule."""
    return path.endswith(".rs") and not production(path) and "test" in path.lower()


def without_inline_tests(text):
    """The file with every `#[cfg(test)]` item blanked line for line, so line numbers hold:
    inline unit tests are tests, not production, whatever file they sit in."""
    lines = text.split("\n")
    out = list(lines)
    i = 0
    while i < len(lines):
        if CFG_TEST.match(lines[i]):
            j, depth, opened = i + 1, 0, False
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                opened = opened or "{" in lines[j]
                if opened and depth <= 0 or not opened and lines[j].rstrip().endswith(";"):
                    break
                j += 1
            for k in range(i, min(j, len(lines) - 1) + 1):
                out[k] = ""
            i = j + 1
        else:
            i += 1
    return "\n".join(out)


def added_production_lines(before, after):
    """Added lines outside inline test modules."""
    return len(mine.added_lines(without_inline_tests(before), without_inline_tests(after)) &
               {n for n, line in enumerate(without_inline_tests(after).split("\n"), 1) if line.strip()})


def env(target=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    e.update(CARGO_HOME=CARGO_HOME, CARGO_TERM_COLOR="never", RUST_BACKTRACE="0")
    e["PATH"] = "/opt/homebrew/bin:" + e.get("PATH", "")
    if target:
        e["CARGO_TARGET_DIR"] = target
    return e


def sh(cmd, cwd, timeout=TIMEOUT, **kw):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, **kw)
    return p.returncode, p.stdout, p.stderr


def tree(clone, sha, target):
    """draw.tree, with every file stamped now: `git archive` keeps commit times, and cargo
    judges freshness by mtime, so an older stamp can pass off a build of other sources."""
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target)
    archive = subprocess.Popen(["git", "-C", clone, "archive", sha], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-m", "-x", "-C", target], stdin=archive.stdout, check=True)
    archive.wait()


def fetch(root):
    """Dependencies into the shared CARGO_HOME, network allowed; a Cargo.lock is written when
    the repository has none. Returns (lock text the tree builds with, None) or (None, why)."""
    had_lock = os.path.isfile(os.path.join(root, "Cargo.lock"))
    code, out, err = sh(["cargo", "fetch"], root, env=env())
    if code:
        return None, "fetch failed: " + err.strip()[-300:]
    with open(os.path.join(root, "Cargo.lock")) as handle:
        lock = handle.read()
    return dict(lock=None if had_lock else lock), "ok"


def apply_recipe(root, recipe):
    """What a tree needs besides its files: the generated Cargo.lock, when there was none."""
    if recipe.get("lock"):
        with open(os.path.join(root, "Cargo.lock"), "w") as handle:
            handle.write(recipe["lock"])


def seed_target(repo, root):
    """The tree's target dir, an APFS clone of the repo's validation target."""
    source, target = os.path.join(TARGETS, repo), os.path.join(root, "target")
    if os.path.isdir(source) and not os.path.exists(target):
        subprocess.run(["cp", "-c", "-R", source, target], check=True)
    return target


def metadata(root):
    code, out, err = sh(["cargo", "metadata", "--offline", "--no-deps", "--format-version", "1"], root, env=env())
    return json.loads(out) if code == 0 else None


def test_targets(root, files):
    """[(package, test target, required features)] that run the tests in `files`: a file under
    a lib's source directory is that lib's unit tests (`lib:<module>`); else the integration
    target whose source is the file, else the targets of its package whose source directory
    holds it, else every integration target of that package (a shared helper module)."""
    meta = metadata(root)
    if not meta:
        return []
    found = []
    for rel in files:
        path = os.path.realpath(os.path.join(root, rel))
        owners = [p for p in meta["packages"] if path.startswith(os.path.dirname(os.path.realpath(p["manifest_path"])) + os.sep)]
        if not owners:
            continue
        package = max(owners, key=lambda p: len(p["manifest_path"]))
        tests = [t for t in package["targets"] if "test" in t["kind"]]
        lib = next((t for t in package["targets"] if "lib" in t["kind"] or "proc-macro" in t["kind"]), None)
        lib_dir = lib and os.path.dirname(os.path.realpath(lib["src_path"])) + os.sep
        if lib_dir and path.startswith(lib_dir):
            found.append((package["name"], "lib:" + module_path(path[len(lib_dir):]), ""))
            continue
        exact = [t for t in tests if os.path.realpath(t["src_path"]) == path]
        near = [t for t in tests if path.startswith(os.path.dirname(os.path.realpath(t["src_path"])) + os.sep)
                and os.path.basename(t["src_path"]) in ("main.rs", "mod.rs")]
        for t in exact or near or tests:
            found.append((package["name"], t["name"], ",".join(t.get("required-features") or [])))
    return sorted(set(found))


def module_path(rel):
    """`routing/tests/mod.rs` → `routing::tests`: the module a file under a lib's source
    directory compiles to."""
    parts = rel[:-3].split(os.sep)
    if parts[-1] in ("mod", "lib"):
        parts = parts[:-1]
    return "::".join(parts)


def run_tests(root, targets, target_dir, timeout=TIMEOUT):
    """{test id: passed} for the given (package, target, features), offline, one target per cargo
    call — `lib:<module>` runs the lib's unit tests and keeps those under that module: a target that does not compile contributes no ids, as an import error under pytest
    sinks only its own file."""
    results = {}
    for package, name, features in targets:
        selector, prefix = (["--lib"], name[4:] + "::") if name.startswith("lib:") else (["--test", name], "")
        selector += ["--all-features"] if features == ALL else ["--features", features] if features else []
        code, out, err = sh(["cargo", "test", "--offline", "--no-fail-fast", "-p", package] + selector +
                            ["--", "--test-threads", "4"], root, timeout=timeout, env=env(target_dir))
        for test, status in TEST_LINE.findall(out):
            if status != "ignored" and test.startswith(prefix):
                results["%s:%s::%s" % (package, name, test)] = status == "ok"
    return results

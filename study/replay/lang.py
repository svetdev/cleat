"""lang — what the replay needs from a language: which files are production and tests, how a
tree gets its dependencies, how its tests run, and what the agent is told and allowed.

TypeScript reuses the paired runs' tsdraw and tsrun; Rust their cargo and rrun.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PAIRED = os.path.join(os.path.dirname(HERE), "paired")
for sub in ("", "typescript", "rust"):
    sys.path.insert(0, os.path.join(PAIRED, sub))
import tsdraw   # noqa: E402
import tsrun    # noqa: E402
import draw     # noqa: E402

_locate, _command, _hint = tsdraw.locate, tsdraw.command, tsdraw.hint


def tsdx_package(root, test_file):
    """The nearest package whose test script is `tsdx test`, or None."""
    d = os.path.dirname(os.path.join(root, test_file))
    while True:
        try:
            scripts = json.load(open(os.path.join(d, "package.json"))).get("scripts") or {}
        except (OSError, ValueError):
            scripts = {}
        if str(scripts.get("test", "")).startswith("tsdx test"):
            return d
        if os.path.normpath(d) == os.path.normpath(root):
            return None
        d = os.path.dirname(d)


def vitest_projects(root):
    """Whether the repository's root vitest config defines its packages as projects: a
    package's own config then merges the root's, whose project globs resolve only from the
    root (zod in 2026: `projects: ["packages/*"]`)."""
    if any(os.path.isfile(os.path.join(root, "vitest.workspace" + ext)) for ext in (".ts", ".js", ".mts", ".mjs", ".json")):
        return True
    for ext in (".ts", ".js", ".mts", ".mjs", ".cts", ".cjs"):
        try:
            if re.search(r"\bprojects\s*:", open(os.path.join(root, "vitest.config" + ext)).read()):
                return True
        except OSError:
            pass
    return False


def locate(root, test_file):
    """tsdraw.locate, but a package tested by `tsdx test` runs through tsdx, which gives jest the
    TypeScript transform that jest alone lacks, even where a jest config sits beside it (trpc in 2021);
    and vitest runs from the root where the root config makes the packages its projects."""
    where = tsdx_package(root, test_file)
    if where and tsdraw.binary_for(root, where, "tsdx"):
        return "tsdx", where
    runner, where = _locate(root, test_file)
    if runner == "vitest" and vitest_projects(root) and tsdraw.binary_for(root, root, "vitest"):
        return "vitest", root
    return runner, where


def command(runner, binary, files, output):
    if runner == "tsdx":
        return [binary, "test", "--ci", "--json", "--outputFile=" + output, "--runTestsByPath", *files]
    return _command(runner, binary, files, output)


def hint(root, files):
    runner, where = locate(root, files[0])
    if runner == "tsdx":
        rel = os.path.relpath(where, root)
        return "npx tsdx test <test file>" if rel == "." else "cd %s && npx tsdx test <test file>" % rel
    return _hint(root, files)


tsdraw.locate, tsdraw.command, tsdraw.hint = locate, command, hint

CAPTURE = threading.local()   # chains run as threads: each captures its own runner output
_sh = tsdraw.sh


def sh(cmd, cwd=None, **kw):
    result = _sh(cmd, cwd, **kw)
    if getattr(CAPTURE, "lines", None) is not None:
        CAPTURE.lines.append("$ %s  (in %s)\nexit %s\n%s" % (" ".join(map(str, cmd)), cwd, result[0], result[1]))
    return result


tsdraw.sh = sh

MANIFESTS = {"ts": ("package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json", ".yarnrc", ".npmrc"),
             "rust": ("Cargo.toml", "Cargo.lock", "rust-toolchain", "rust-toolchain.toml")}


class TypeScript:
    name = "ts"
    public = re.compile(r"^\+\s*export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?"
                        r"(?:function\*?|const|let|var|class|interface|type|enum|namespace)\s+(\w+)", re.M)
    production = staticmethod(tsdraw.production)
    is_test = staticmethod(tsdraw.is_test)
    prompt = tsrun.PROMPT
    allowed = tsrun.ALLOWED
    ignored = tsrun.IGNORED
    tools = (tsdraw.TOOLS, tsdraw.STORE)   # package managers and the pnpm store, which the agent's installs read

    @staticmethod
    def install(root, recipe=None):
        """(recipe, why): the tree's dependencies, the recipe that worked. Where every install
        fails, the tree's own lockfile is installed without lifecycle scripts and the root
        package's postinstall and prepare run alone: a native module some example app pulls
        in (trpc's sharp, on Node 26) then no longer stops the workspace from being linked."""
        TypeScript.drop_build(root)   # from scratch: an install over an older tree's modules left trpc's stale
        code = TypeScript.run_recipe(root, recipe) if recipe else None
        recipe, why = (recipe, "ok") if code == 0 else tsdraw.install(root)
        if recipe is None:
            recipe, why = TypeScript.without_scripts(root, why)
        elif "postinstall" not in recipe.get("after", []):
            TypeScript.postinstall(root, recipe)
        tsrun.clear_hooks(root)
        return recipe, why

    @staticmethod
    def before_check(root):
        """The root postinstall again once the code is in: trpc's preconstruct dev links only the
        entry points whose source exists, and a change's install runs before its code lands."""
        name, version = tsdraw.manager(root)
        if name:
            TypeScript.postinstall(root, dict(manager=name, version=version))

    @staticmethod
    def postinstall(root, recipe):
        """The root package's postinstall, run again: yarn 1 skips it when the lockfile has not
        moved, though a workspace package's entry points have (trpc's preconstruct dev links them)."""
        scripts = (tsdraw.read_json(os.path.join(root, "package.json")) or {}).get("scripts") or {}
        if "postinstall" in scripts:
            tsdraw.sh([recipe["manager"], "run", "postinstall"], cwd=root, timeout=tsdraw.INSTALL_TIMEOUT,
                      extra_path=tsdraw.recipe_path(recipe))

    @staticmethod
    def run_recipe(root, recipe):
        code = tsdraw.install_recipe(root, recipe)[0]
        for script in recipe.get("after", []) if code == 0 else []:
            code = tsdraw.sh([recipe["manager"], "run", script], cwd=root, timeout=tsdraw.INSTALL_TIMEOUT,
                             extra_path=tsdraw.recipe_path(recipe))[0]
            if code != 0:
                break
        return code

    @staticmethod
    def without_scripts(root, why):
        name, version = tsdraw.manager(root)
        if name not in ("yarn", "npm", "pnpm") or (name == "yarn" and not str(version).startswith("1.")):
            return None, why
        cmd = {"yarn": ["yarn", "install", "--frozen-lockfile", "--non-interactive", "--ignore-engines"],
               "npm": ["npm", "ci", "--no-audit", "--no-fund"],
               "pnpm": ["pnpm", "install", "--frozen-lockfile", "--store-dir", tsdraw.STORE,
                        "--config.confirmModulesPurge=false"]}[name] + ["--ignore-scripts"]
        scripts = (tsdraw.read_json(os.path.join(root, "package.json")) or {}).get("scripts") or {}
        recipe = dict(manager=name, version=version, cmd=cmd, after=[s for s in ("postinstall", "prepare") if s in scripts])
        if TypeScript.run_recipe(root, recipe) != 0:
            return None, why + "; without scripts failed too"
        return recipe, "ok without scripts"

    @staticmethod
    def validate(clones, work, cand):
        """(task fields, why): the change's tests fail before it and pass after it on upstream's tree.
        tsdraw.validate's rule, with the dependencies reinstalled when the change moves a manifest,
        as a chain reinstalls them (trpc's changes add the packages their tests import)."""
        clone = os.path.join(clones, cand["repo"])
        root = os.path.join(work, "validate", cand["repo"] + "-" + cand["sha"][:8])
        try:
            draw.tree(clone, cand["parent"], root)
            subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
            recipe, why = TypeScript.install(root)
            if recipe is None:
                return None, why
            others = [p for _, _, p in cand["files"] if not tsdraw.production(p)]
            prods = [p for _, _, p in cand["files"] if tsdraw.production(p)]
            deleted = {q for s, _, q in cand["files"] if s == "D"}
            tests = [p for p in others if tsdraw.is_test(p) and p not in deleted]
            if not tests:
                return None, "no runnable test file"
            draw.overlay(clone, cand["sha"], root, others)
            if any(os.path.basename(p) in MANIFESTS["ts"] for p in others):
                recipe, why = TypeScript.install(root, recipe)
                if recipe is None:
                    return None, "reinstall failed: " + why
            if not any(tsdraw.locate(root, t)[0] for t in tests):
                return None, "no test runner found"
            before = tsdraw.run_tests(root, tests, "before")
            draw.overlay(clone, cand["sha"], root, prods)
            after = tsdraw.run_tests(root, tests, "after")
            if not after:
                return None, "tests did not run at the commit"
            if not all(after.values()):
                return None, "%d test(s) fail at the commit" % sum(1 for v in after.values() if not v)
            f2p = sorted(t for t, ok in after.items() if ok and not before.get(t, False))
            p2p = sorted(t for t, ok in after.items() if ok and before.get(t, False))
            if not f2p:
                return None, "no fail-to-pass test"
            return dict(recipe=recipe, tests=tests, f2p=f2p, p2p=p2p, overlay=others, hint=tsdraw.hint(root, tests)), "ok"
        finally:
            shutil.rmtree(root, ignore_errors=True)

    keep_own = ()
    # test-runner configs: .js files, so production by tsdraw's rule, but what upstream's tests
    # need to load at all (trpc's packages/server/jest.config.js); a check lays upstream's over
    runner_config = re.compile(r"(^|/)((jest|babel|vitest)\.config\.[cm]?[jt]s|\.babelrc(\.json|\.js)?)$")

    @staticmethod
    def run_tests(root, change, label):
        """{test id: passed} for a validated change's tests; the runner's output in `output`."""
        CAPTURE.lines = []
        try:
            return tsdraw.run_tests(root, change["tests"], label)
        finally:
            CAPTURE.last, CAPTURE.lines = CAPTURE.lines, None

    @staticmethod
    def output():
        """This thread's last run_tests output, which a check keeps beside its score."""
        return getattr(CAPTURE, "last", None) or []

    @staticmethod
    def suite(root):
        """The tree's own checks, as its CI would run them: every test file it holds, and the root
        type-check where there is one. Compile errors are kept without their positions, so a line
        moved above one is not a new error; `silent` are the test files that reported no test."""
        listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root,
                                capture_output=True, text=True).stdout.split("\n")
        files = sorted({p for p in listed if p and tsdraw.is_test(p) and os.path.isfile(os.path.join(root, p))})
        tests = TypeScript.run_tests(root, dict(tests=files), "suite")
        errors = []
        tsc = tsdraw.binary_for(root, root, "tsc")
        if tsc and os.path.isfile(os.path.join(root, "tsconfig.json")):
            p = subprocess.run([tsc, "--noEmit", "-p", "tsconfig.json", "--pretty", "false"], cwd=root,
                               capture_output=True, text=True, timeout=tsdraw.TEST_TIMEOUT)
            errors = sorted({re.sub(r"\(\d+,\d+\)", "", line) for line in p.stdout.split("\n") if ": error TS" in line})
        silent = [f for f in files if not any(t.startswith(f + "::") for t in tests)]
        return dict(tests=tests, errors=errors, silent=silent)

    @staticmethod
    def hint(root, files):
        return tsdraw.hint(root, files)

    @staticmethod
    def env(recipe):
        return tsrun.agent_env(dict(recipe=recipe))

    @staticmethod
    def drop_build(root):
        tsrun.drop_modules(root)

    @staticmethod
    def sandbox_extra(settings):
        pass


def rust():
    """Rust, built on first use: importing the paired runs' rrun patches their shared modules
    for Rust, which a TypeScript replay must not see."""
    import cargo
    import rrun

    def sh(cmd, root, env):
        try:
            p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=cargo.TIMEOUT, env=env)
        except subprocess.TimeoutExpired:
            return "timeout", ""
        return p.returncode, (p.stdout[-2000:] + p.stderr[-2000:])

    class Rust:
        name = "rust"
        public = re.compile(r"^\+\s*pub\s+(?:async\s+)?(?:unsafe\s+)?(?:fn|struct|enum|trait|type|const|static|mod)\s+(\w+)", re.M)
        production = staticmethod(cargo.production)
        is_test = staticmethod(cargo.is_test)
        prompt = rrun.PROMPT
        allowed = rrun.ALLOWED
        ignored = ["/target/", "quality/.running/", "quality/.events.jsonl", ".cleat-gate-*.json"]
        runner_config = re.compile(r"$^")
        tools = (cargo.CARGO_HOME,)   # the registry the agent's offline builds read
        keep_own = ("Cargo.lock",)   # the tree's lock, updated once at the start; upstream's would undo it
        warm = None                  # a built target dir to clone into the next tree

        @staticmethod
        def install(root, recipe=None, update=False):
            """Dependencies fetched into the shared CARGO_HOME and the tree built into its own
            target/ (starship's tests run ./target/debug/starship); `update` moves the lock to the
            newest semver-compatible versions, which the 2019 locks need to build on Rust 1.99."""
            target = os.path.join(root, "target")
            if Rust.warm and not os.path.exists(target):
                subprocess.run(["cp", "-cR", Rust.warm, target])
            env = cargo.env(target)
            if update:
                code, said = sh(["cargo", "update"], root, env)
                if code != 0:
                    return None, "cargo update failed: " + said[-300:]
            code, said = sh(["cargo", "test", "--no-run", "--workspace"], root, env)
            if code != 0:
                return None, "build failed: " + said[-300:]
            return dict(update=update), "ok"

        @staticmethod
        def validate(clones, work, cand):
            clone = os.path.join(clones, cand["repo"])
            root = os.path.join(work, "validate", cand["repo"] + "-" + cand["sha"][:8])
            cargo.tree(clone, cand["parent"], root)
            try:
                recipe, why = Rust.install(root, update=True)
                if not recipe:
                    return None, why
                warm = os.path.join(work, "warm-target")
                shutil.rmtree(warm, ignore_errors=True)
                subprocess.run(["cp", "-cR", os.path.join(root, "target"), warm])
                Rust.warm = warm
                others = [p for _, _, p in cand["files"] if not cargo.production(p) and os.path.basename(p) != "Cargo.lock"]
                prods = [p for _, _, p in cand["files"] if cargo.production(p)]
                deleted = {q for s, _, q in cand["files"] if s == "D"}
                tests = [p for p in others if cargo.is_test(p) and p not in deleted]
                draw.overlay(clone, cand["sha"], root, others)
                if any(os.path.basename(p) == "Cargo.toml" for p in others):
                    Rust.install(root)
                targets = cargo.test_targets(root, tests)
                if not targets:
                    return None, "no runnable test target"
                change = dict(targets=targets)
                before = Rust.run_tests(root, change, "before")
                draw.overlay(clone, cand["sha"], root, prods)
                after = Rust.run_tests(root, change, "after")
            finally:
                shutil.rmtree(root, ignore_errors=True)
            if not after:
                return None, "tests did not run at the commit"
            # a test failing at upstream's own commit (starship's tests of the ruby or go on this
            # machine) cannot tell one tree from another: it is left out, not held against the change
            unscored = sorted(t for t, ok in after.items() if not ok)
            f2p = sorted(t for t, ok in after.items() if ok and not before.get(t, False))
            p2p = sorted(t for t, ok in after.items() if ok and before.get(t, False))
            if not f2p:
                return None, "no fail-to-pass test"
            return dict(recipe=recipe, tests=tests, targets=targets, f2p=f2p, p2p=p2p, overlay=others,
                        unscored=unscored, hint="cargo test"), "ok"

        @staticmethod
        def output():
            return []

        @staticmethod
        def run_tests(root, change, label):
            try:
                return cargo.run_tests(root, [tuple(t) for t in change["targets"]], os.path.join(root, "target"))
            except subprocess.TimeoutExpired:
                return {}

        @staticmethod
        def suite(root):
            raise SystemExit("rust: the acceptance suite is not built yet")

        @staticmethod
        def hint(root, files):
            return "cargo test"

        @staticmethod
        def env(recipe):
            return rrun.clean_env()

        @staticmethod
        def drop_build(root):
            shutil.rmtree(os.path.join(root, "target"), ignore_errors=True)

        @staticmethod
        def before_check(root):
            pass

        @staticmethod
        def sandbox_extra(settings):
            settings["sandbox"]["filesystem"]["allowWrite"] = [os.path.expanduser("~/Library/Caches")]

    return Rust


def get(name):
    if name == "ts":
        return TypeScript
    if name == "rust":
        return rust()
    raise SystemExit("language %r: not built" % name)

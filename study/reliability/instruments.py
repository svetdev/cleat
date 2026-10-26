#!/usr/bin/env python3
"""instruments — agreement of lizard (through cleat's extractor) with an independent counter.

python      radon's cc_visit over the same orig sample, functions matched by (file, def line).
            radon adds a closure's complexity to its enclosing function and lizard does not, so
            the agreement is reported over all matched functions and over those with no nested def.
swift       SwiftLint's cyclomatic_complexity at threshold 1, over Alamofire/Source and Nuke/Sources,
            with ignores_case_statements as cleat's config sets it (true) and as SwiftLint ships it
            (false); matched by file and nearest declaration line (within 3).
typescript  ESLint 9's core `complexity` rule at 0 (typescript-eslint parser) over zod's
            packages/zod/src (tests and benchmarks left out); matched by file and nearest line (within 2).

Statistics: ICC(2,1) (two-way random effects, absolute agreement, single measure), Lin's
concordance, Pearson r, mean difference, share identical, and Cohen's kappa on cc > 8.
Writes data/q3.json.
"""
import ast, json, os, re, subprocess, sys, tempfile, shutil
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
from radon.complexity import cc_visit

TMP = os.path.join(common.Q3, "tmp")
NODE = os.path.join(common.Q3, "node")


def icc21(x, y):
    m = np.column_stack([x, y]).astype(float)
    n, k = m.shape
    gm = m.mean()
    ssr = k * ((m.mean(axis=1) - gm) ** 2).sum()
    ssc = n * ((m.mean(axis=0) - gm) ** 2).sum()
    sst = ((m - gm) ** 2).sum()
    sse = sst - ssr - ssc
    msr, msc, mse = ssr / (n - 1), ssc / (k - 1), sse / ((n - 1) * (k - 1))
    return (msr - mse) / (msr + (k - 1) * mse + k * (msc - mse) / n)


def lin(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    return 2 * np.cov(x, y, bias=True)[0, 1] / (x.var() + y.var() + (x.mean() - y.mean()) ** 2)


def kappa(a, b):
    a, b = np.asarray(a, bool), np.asarray(b, bool)
    po = (a == b).mean()
    pe = a.mean() * b.mean() + (1 - a.mean()) * (1 - b.mean())
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def stats(x, y, label_x, label_y):
    x, y = np.asarray(x), np.asarray(y)
    ox, oy = x > common.CC_CEIL, y > common.CC_CEIL
    return {"n": int(len(x)), "x": label_x, "y": label_y, "icc_2_1": round(float(icc21(x, y)), 4),
            "lin_ccc": round(float(lin(x, y)), 4), "pearson": round(float(np.corrcoef(x, y)[0, 1]), 4),
            "mean_x": round(float(x.mean()), 3), "mean_y": round(float(y.mean()), 3),
            "mean_diff_x_minus_y": round(float((x - y).mean()), 3), "identical": round(float((x == y).mean()), 4),
            "over8_x": int(ox.sum()), "over8_y": int(oy.sum()), "over8_both": int((ox & oy).sum()),
            "kappa_over8": round(float(kappa(ox, oy)), 4), "verdict_agreement": round(float((ox == oy).mean()), 4),
            "diff_histogram": {str(k): int(v) for k, v in zip(*np.unique(x - y, return_counts=True))}}


# ---------------------------------------------------------------- python

def has_nested_def(fn):
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) for n in ast.walk(fn) if n is not fn)


def python():
    import pickle
    with open(os.path.join(common.DATA, "meas", "orig.pkl"), "rb") as h:
        orig = pickle.load(h)
    lx, rx, lx_flat, rx_flat = [], [], [], []
    unmatched = 0
    radon_only = 0
    for c, rec in orig.items():
        by_line = {}
        for (rel, key), v in rec["cx"].items():
            by_line[(rel, v[2])] = v[0]
        root = os.path.join(common.DATA, "orig", c)
        for rel in rec["index"]:
            text = open(os.path.join(root, rel), encoding="utf-8").read()
            try:
                blocks = cc_visit(text)
            except Exception:
                continue
            tree = ast.parse(text)
            nested = {n.lineno: has_nested_def(n) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            funcs = []
            for b in blocks:
                if hasattr(b, "methods"):          # a class: its methods
                    funcs += b.methods
                else:
                    funcs.append(b)
            funcs = [f for f in funcs if not hasattr(f, "methods")]
            seen = set()
            for f in funcs:
                if f.lineno in seen:          # cc_visit lists a method both alone and under its class
                    continue
                seen.add(f.lineno)
                l = by_line.get((rel, f.lineno))
                if l is None:
                    radon_only += 1
                    continue
                lx.append(l)
                rx.append(f.complexity)
                if not nested.get(f.lineno, False):
                    lx_flat.append(l)
                    rx_flat.append(f.complexity)
    total_lizard = sum(len(r["cx"]) for r in orig.values())
    return {"matched": len(lx), "lizard_functions": total_lizard, "radon_functions_unmatched": radon_only,
            "note": "lizard lists nested functions separately and radon folds them into the enclosing function, so lizard has more rows",
            "all_matched": stats(lx, rx, "lizard", "radon"),
            "without_nested_defs": stats(lx_flat, rx_flat, "lizard", "radon")}


# ---------------------------------------------------------------- swift

def swiftlint_cc(roots, ignores_case):
    cfg = ("only_rules: [cyclomatic_complexity, function_body_length]\n"
           "cyclomatic_complexity: {warning: 1, error: 1, ignores_case_statements: %s}\n"
           "function_body_length: {warning: 1, error: 1}\n" % ("true" if ignores_case else "false"))
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as h:
        h.write(cfg)
        path = h.name
    p = subprocess.run(["swiftlint", "lint", "--quiet", "--reporter", "json", "--config", path, *roots], capture_output=True, text=True)
    os.unlink(path)
    return common.complexity.functions_from_swiftlint(json.loads(p.stdout or "[]"))


def nearest_match(a, b, window):
    """Pair each (file, line, cc) of a with the nearest unpaired b in the same file within window."""
    byf = {}
    for i, (f, l, v) in enumerate(b):
        byf.setdefault(f, []).append((l, i, v))
    used, pairs = set(), []
    for f, l, v in a:
        cands = [(abs(l2 - l), i, v2) for l2, i, v2 in byf.get(f, []) if abs(l2 - l) <= window and i not in used]
        if cands:
            d, i, v2 = min(cands)
            used.add(i)
            pairs.append((v, v2))
    return pairs


def swift():
    roots = [os.path.join(TMP, "alamofire", "Source"), os.path.join(TMP, "nuke", "Sources")]
    spec = {"tool": "lizard", "languages": ["swift"], "exclude": []}
    fns, _, _, version = common.complexity.measure(spec, roots, [], root=TMP)
    liz = [(os.path.realpath(f.path), f.line, f.cc) for f in fns]
    out = {"lizard_version": version, "lizard_functions": len(liz),
           "commits": {r: subprocess.check_output(["git", "-C", os.path.join(TMP, r), "rev-parse", "HEAD"], text=True).strip() for r in ("alamofire", "nuke")}}
    sv = subprocess.run(["swiftlint", "version"], capture_output=True, text=True).stdout.strip()
    out["swiftlint_version"] = sv
    for ignores in (True, False):
        sl = [(os.path.realpath(f.path), f.line, f.cc) for f in swiftlint_cc(roots, ignores)]
        pairs = nearest_match(sl, liz, 3)
        key = "ignores_case_statements_%s" % str(ignores).lower()
        out[key] = {"swiftlint_functions": len(sl), "matched": len(pairs),
                    "agreement": stats([p[1] for p in pairs], [p[0] for p in pairs], "lizard", "swiftlint")}
    return out


# ---------------------------------------------------------------- typescript

def typescript():
    src = os.path.join(TMP, "zod", "packages", "zod", "src")
    work = os.path.join(NODE, "tsrc")   # under the eslint config's base path
    if os.path.exists(work):
        shutil.rmtree(work)
    for d, dirs, files in os.walk(src):
        dirs[:] = [x for x in dirs if x not in ("tests", "__tests__", "benchmarks", "node_modules")]
        for f in files:
            if f.endswith(".ts") and not f.endswith((".test.ts", ".d.ts")):
                rel = os.path.relpath(os.path.join(d, f), src)
                os.makedirs(os.path.join(work, os.path.dirname(rel)), exist_ok=True)
                shutil.copy(os.path.join(d, f), os.path.join(work, rel))
    spec = {"tool": "lizard", "languages": ["typescript"], "exclude": []}
    fns, _, _, version = common.complexity.measure(spec, [work], [], root=work)
    liz = [(os.path.realpath(f.path), f.line, f.cc) for f in fns]
    cfg = os.path.join(NODE, "eslint.config.mjs")
    with open(cfg, "w") as h:
        h.write('import tsParser from "@typescript-eslint/parser";\n'
                'export default [{ files: ["**/*.ts"], languageOptions: { parser: tsParser }, linterOptions: { noInlineConfig: true, reportUnusedDisableDirectives: "off" },\n'
                '  rules: { complexity: ["error", 0] } }];\n')
    p = subprocess.run([os.path.join(NODE, "node_modules/.bin/eslint"), "--no-config-lookup", "-c", cfg, "-f", "json", work],
                       capture_output=True, text=True, cwd=NODE)
    report = json.loads(p.stdout or "[]")
    es = []
    for f in report:
        for m in f["messages"]:
            if m.get("ruleId") == "complexity":
                cc = re.search(r"complexity of (\d+)", m["message"])
                if cc:
                    es.append((os.path.realpath(f["filePath"]), m["line"], int(cc.group(1))))
    pairs = nearest_match(es, liz, 2)
    ev = subprocess.run([os.path.join(NODE, "node_modules/.bin/eslint"), "--version"], capture_output=True, text=True).stdout.strip()
    return {"lizard_version": version, "eslint_version": ev, "files": sum(len(f) for _, _, f in os.walk(work)),
            "commit": subprocess.check_output(["git", "-C", os.path.join(TMP, "zod"), "rev-parse", "HEAD"], text=True).strip(),
            "lizard_functions": len(liz), "eslint_functions": len(es), "matched": len(pairs),
            "eslint_parse_errors": sum(1 for f in report for m in f["messages"] if m.get("fatal")),
            "agreement": stats([p[1] for p in pairs], [p[0] for p in pairs], "lizard", "eslint")}


def main():
    res = {"python": python()}
    print("python", json.dumps(res["python"]["all_matched"])[:300], file=sys.stderr)
    res["swift"] = swift()
    res["typescript"] = typescript()
    res["no_independent_counter_on_this_machine"] = {
        "rust": "no cargo/rustc/clippy installed (clippy's cognitive_complexity is not cyclomatic anyway)",
        "go": "no go toolchain, so no gocyclo",
        "kotlin": "no kotlinc/detekt", "java": "no JDK tools beyond the /usr/bin/java stub; no PMD/checkstyle",
        "ruby": "system ruby only, rubocop not installed (installing gems system-wide was not done)",
        "shell": "no complexity gate for shell (lizard does not read it; attach does not configure one)"}
    with open(os.path.join(common.DATA, "q3.json"), "w") as h:
        json.dump(res, h, indent=1)


if __name__ == "__main__":
    main()

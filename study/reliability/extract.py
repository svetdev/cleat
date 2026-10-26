#!/usr/bin/env python3
"""extract — the mechanical extract-method dodge over every function of the orig sample that
is over cyclomatic 8 or 60 lines.

One step: the function's largest branch body (the statements of an if/elif/else arm, a
for/while body, an except handler, or a match case — by line span; the largest eligible one)
is moved verbatim into a new module-level helper with one caller. Inputs are the function's
locals (and enclosing-scope free names) the body reads before writing; outputs are the locals
it writes that the function reads elsewhere, returned as a tuple. A body is ineligible when it
contains return/yield, a break/continue that leaves it, global/nonlocal, zero-arg super(),
a name-mangled __name, locals()/vars(), or (in a sync function) await. The result must parse
and compile; runtime equivalence is not tested.

Iterated: the step repeated, choosing the body with the most decision points while cc is over
(the largest while only length is), (each time on the original function) until lizard says it is
under both ceilings, no eligible body is left, or a step gains nothing (cc not lower and
length down by at most one line — the step is then undone), at most 12 steps.

Writes data/extract1/<clone>/… and data/extractN/<clone>/… (only files with an over-ceiling
function), and data/extract-plan.json (per function: steps, helpers' names, why it stopped).
"""
import ast, builtins, json, os, pickle, re, shutil, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
sys.path.insert(0, os.path.join(common.S, "venv/lib/python3.14/site-packages"))
import lizard

NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
BUILTINS = set(dir(builtins))
MAX_STEPS = 12


def walk_own(nodes):
    """Every node under `nodes`, not descending into nested def/class/lambda (the nested node
    itself is yielded)."""
    stack = list(nodes)
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, NESTED):
            stack.extend(ast.iter_child_nodes(n))


def find_function(tree, key):
    idx = common.def_index(ast.unparse(tree)) if False else None
    seen = {}
    found = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = prefix + child.name
                if not isinstance(child, ast.ClassDef):
                    k = "%s#%d" % (q, seen.get(q, 0))
                    seen[q] = seen.get(q, 0) + 1
                    if k == key:
                        found.append(child)
                visit(child, q + ".")
            else:
                visit(child, prefix)
    visit(tree, "")
    return found[0] if found else None


def bodies(fn):
    """[(stmts)] of every branch body in fn's own scope."""
    out = []
    for n in walk_own(fn.body):
        if isinstance(n, NESTED):
            continue
        if isinstance(n, ast.If):
            out.append(n.body)
            if n.orelse and not (len(n.orelse) == 1 and isinstance(n.orelse[0], ast.If)):
                out.append(n.orelse)
        elif isinstance(n, (ast.For, ast.AsyncFor, ast.While)):
            out.append(n.body)
            if n.orelse:
                out.append(n.orelse)
        elif isinstance(n, (ast.Try, ast.TryStar)):
            out += [h.body for h in n.handlers]
        elif isinstance(n, ast.Match):
            out += [c.body for c in n.cases]
    return out


def span(stmts):
    return stmts[0].lineno, max(s.end_lineno for s in stmts)


def ineligible(stmts, fn):
    loop_depth_ok = True

    def bad(node, in_loop):
        if isinstance(node, NESTED):
            return None
        if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)):
            return "return/yield"
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            return "global/nonlocal"
        if isinstance(node, (ast.Break, ast.Continue)) and not in_loop:
            return "break/continue leaves it"
        if isinstance(node, ast.Await) and not isinstance(fn, ast.AsyncFunctionDef):
            return "await"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("super", "locals", "vars") and not node.args:
            return "super()/locals()"
        if isinstance(node, ast.Name) and node.id.startswith("__") and not node.id.endswith("__"):
            return "mangled"
        if isinstance(node, ast.Attribute) and node.attr.startswith("__") and not node.attr.endswith("__"):
            return "mangled"
        loop = in_loop or isinstance(node, (ast.For, ast.AsyncFor, ast.While))
        for c in ast.iter_child_nodes(node):
            # a loop's else/orelse is not inside the loop for break purposes; close enough
            r = bad(c, loop)
            if r:
                return r
        return None
    for s in stmts:
        r = bad(s, False)
        if r:
            return r
    return None


def fn_locals(fn):
    names = {a.arg for a in ast.walk(fn.args) if isinstance(a, ast.arg)}
    for n in walk_own(fn.body):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in n.names)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
            names.add(n.name)
    return names


def module_names(tree):
    names = set()
    for n in tree.body:
        for m in ast.walk(n) if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else [n]:
            if isinstance(m, ast.Name) and isinstance(m.ctx, ast.Store):
                names.add(m.id)
            elif isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(m.name)
            elif isinstance(m, (ast.Import, ast.ImportFrom)):
                names.update((a.asname or a.name).split(".")[0] for a in m.names)
    return names


def occurrences(stmts):
    """[(line, col, name, 'load'|'store')] for every Name in stmts (nested scopes included —
    a nested lambda reading a local still needs it passed), in source order."""
    occ = []
    for s in stmts:
        for n in ast.walk(s):
            if isinstance(n, ast.Name):
                occ.append((n.lineno, n.col_offset, n.id, "load" if isinstance(n.ctx, ast.Load) else "store"))
            elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
                occ.append((n.target.lineno, n.target.col_offset - 1, n.target.id, "load"))
            elif isinstance(n, ast.ExceptHandler) and n.name:
                occ.append((n.lineno, n.col_offset + 1, n.name, "store"))
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    occ.append((n.lineno, n.col_offset, (a.asname or a.name).split(".")[0], "store"))
            elif isinstance(n, ast.arg):
                occ.append((n.lineno, n.col_offset, n.arg, "argstore"))
    occ.sort()
    return occ


def decisions(stmts):
    """Decision points inside stmts (own scope) — what lizard would move out with them."""
    n = 0
    for x in walk_own(stmts):
        if isinstance(x, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.ExceptHandler, ast.match_case)):
            n += 1
        elif isinstance(x, ast.BoolOp):
            n += len(x.values) - 1
        elif isinstance(x, ast.comprehension):
            n += len(x.ifs)
    return n


def extract_once(text, key, counter, rank="span"):
    """(new text, helper name) or (None, reason). rank "span": the largest body by lines (the
    one-step rule); "decisions": the body holding the most decision points, then the largest."""
    tree = ast.parse(text)
    fn = find_function(tree, key)
    if fn is None:
        return None, "function not found"
    lines = text.split("\n")
    if rank == "span":
        cands = sorted(bodies(fn), key=lambda b: -(span(b)[1] - span(b)[0]))
    else:
        cands = sorted(bodies(fn), key=lambda b: (-decisions(b), -(span(b)[1] - span(b)[0])))
    reason = "no branch body"
    for stmts in cands:
        why = ineligible(stmts, fn)
        if why:
            reason = why
            continue
        first = stmts[0]
        l0, l1 = span(stmts)
        indent = lines[l0 - 1][:len(lines[l0 - 1]) - len(lines[l0 - 1].lstrip())]
        if first.col_offset != len(indent.encode()):
            reason = "body on the header line"
            continue
        if any(s.end_lineno == l1 for s in stmts) and len(lines[l1 - 1].encode()) > max(s.end_col_offset for s in stmts if s.end_lineno == l1) and lines[l1 - 1].encode()[max(s.end_col_offset for s in stmts if s.end_lineno == l1):].strip() and not lines[l1 - 1].encode()[max(s.end_col_offset for s in stmts if s.end_lineno == l1):].strip().startswith(b"#"):
            reason = "code after the body on its last line"
            continue
        floc = fn_locals(fn)
        mod = module_names(tree)
        occ = occurrences(stmts)
        inner_args = {n for _, _, n, k in occ if k == "argstore"}
        first_kind = {}
        for _, _, n, k in occ:
            first_kind.setdefault(n, k)
        loaded = {n for _, _, n, k in occ if k == "load"}
        stored = {n for _, _, n, k in occ if k == "store"}
        def needed(n):
            return n in floc or (n not in mod and n not in BUILTINS)
        inputs = sorted(n for n in loaded if first_kind.get(n) == "load" and needed(n) and n not in inner_args)
        # reads elsewhere in fn of the names the body writes
        outside = set()
        for n in walk_own(fn.body):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and not (l0 <= n.lineno <= l1):
                outside.add(n.id)
        for n in ast.walk(fn):  # nested scopes reading it
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and not (l0 <= n.lineno <= l1):
                outside.add(n.id)
        outputs = sorted(n for n in stored if n in floc and n in outside)
        # an output bound before the body is passed in too, so a path that skips the store keeps it
        before = set()
        for n in walk_own(fn.body):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.lineno < l0:
                before.add(n.id)
        before |= {a.arg for a in ast.walk(fn.args) if isinstance(a, ast.arg)}
        inputs = sorted(set(inputs) | {o for o in outputs if o in before})
        is_async = any(isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for s in stmts for n in walk_own([s]))
        counter[0] += 1
        hname = "_q3x_%s_%d" % (fn.name.strip("_") or "f", counter[0])
        body = []
        for ln in lines[l0 - 1:l1]:
            body.append("    " + ln[len(indent):] if ln.startswith(indent) else ln)
        if outputs:
            body.append("    return " + (outputs[0] if len(outputs) == 1 else "(%s)" % ", ".join(outputs)))
        helper = ["", "", "%sdef %s(%s):" % ("async " if is_async else "", hname, ", ".join(inputs))] + body
        call = "%s(%s)" % (hname, ", ".join(inputs))
        if is_async:
            call = "await " + call
        if outputs:
            call = "%s = %s" % (outputs[0] if len(outputs) == 1 else ", ".join(outputs), call)
        new_lines = lines[:l0 - 1] + [indent + call] + lines[l1:]
        while new_lines and new_lines[-1] == "":
            new_lines.pop()
        new = "\n".join(new_lines + helper) + "\n"
        try:
            compile(new, "<extract>", "exec")
        except SyntaxError as e:
            reason = "does not compile: %s" % e.msg
            counter[0] -= 1
            continue
        return new, hname
    return None, reason


def lizard_numbers(text, key, helpers):
    """(cc, length) of the function `key` and {helper: (cc, length)} by lizard (python API)."""
    info = lizard.analyze_file.analyze_source_code("x.py", text)
    idx = common.def_index(text)
    by_line = {f.start_line: f for f in info.function_list}
    fn = None
    for s, e, k in idx:
        if k == key and s in by_line:
            fn = by_line[s]
    hs = {}
    for f in info.function_list:
        if f.name in helpers:
            hs[f.name] = (f.cyclomatic_complexity, f.length)
    return ((fn.cyclomatic_complexity, fn.length) if fn else None), hs


def main():
    with open(os.path.join(common.DATA, "meas/orig.pkl"), "rb") as h:
        orig = pickle.load(h)
    plan = []
    for variant in ("extract1", "extractN"):
        root = os.path.join(common.DATA, variant)
        if os.path.exists(root):
            shutil.rmtree(root)
    for c, rec in orig.items():
        by_file = {}
        for (rel, key), v in rec["cx"].items():
            if common.over(v) and not key.startswith("@"):
                by_file.setdefault(rel, []).append((key, v))
        for rel, fns in sorted(by_file.items()):
            with open(os.path.join(common.DATA, "orig", c, rel), encoding="utf-8") as h:
                text = h.read()
            t1, tn = text, text
            counter = [0]
            for key, v in fns:
                entry = {"clone": c, "file": rel, "key": key, "cc": v[0], "lines": v[1], "helpers1": [], "helpersN": []}
                new, hn = extract_once(t1, key, counter)
                if new is None:
                    entry["why_not1"] = hn
                else:
                    t1 = new
                    entry["helpers1"].append(hn)
                # iterated, on its own copy
                steps = 0
                while steps < MAX_STEPS:
                    num, _ = lizard_numbers(tn, key, [])
                    if num is None or (num[0] <= common.CC_CEIL and num[1] <= common.LINE_CEIL):
                        break
                    new, hn = extract_once(tn, key, counter, rank="decisions" if num[0] > common.CC_CEIL else "span")
                    if new is None:
                        entry["why_stopN"] = hn
                        break
                    after, _ = lizard_numbers(new, key, [])
                    if after is None or (after[0] >= num[0] and after[1] >= num[1] - 1):
                        entry["why_stopN"] = "no progress"   # a one-line body moved: nothing gained
                        break
                    tn = new
                    entry["helpersN"].append(hn)
                    steps += 1
                plan.append(entry)
            for variant, t in (("extract1", t1), ("extractN", tn)):
                dest = os.path.join(common.DATA, variant, c, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w", encoding="utf-8") as h:
                    h.write(t)
        print(c, len(by_file), file=sys.stderr)
    with open(os.path.join(common.DATA, "extract-plan.json"), "w") as h:
        json.dump(plan, h, indent=0)


if __name__ == "__main__":
    main()

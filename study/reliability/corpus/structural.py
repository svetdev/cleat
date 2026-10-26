"""structural — the complexity, duplication, layering and dead-symbol seeds, per language,
generated from the clean templates by fixed rules (no seed was edited after a gate run).

Complexity: every seed is a function with at least 8 decision points by McCabe's counting
(cyclomatic 9, the ceiling being 8), each built from one kind of construct, plus a function
of 62+ lines with none (the length ceiling is 60), plus an existing function grown past the
ceiling in place.
Duplication: copies of the template's `summarize` function — whole, re-indented, into another
layer's file, and its first six significant lines — verbatim (Type-1) and with every local
identifier renamed (Type-2).
Layering: a reference from `models` up to `services`, the layer it may not reach, in the
import form the reader is built for and in a second form it may not be.
Dead symbols: an unreferenced function and an unreferenced type, plus a dead function whose
name is all lowercase and a dead function whose name happens to be used elsewhere.
"""
import re
from templates import T, STATS_FILE, MODEL_FILE, EXT

N = 8  # decision points per complexity seed


def ind(text, n):
    return "\n".join((" " * n + l) if l else l for l in text.split("\n"))


def tab(text):
    return "\n".join(("\t" + l) if l else l for l in text.split("\n"))


# Per language: how to write a function and each construct. `w` wraps a body into a function.
def py():
    w = lambda name, body: "def %s(x, y):\n%s\n    return y" % (name, ind(body, 4))
    return dict(
        w=w,
        if_=lambda i: "if x == %d:\n    y = %d" % (i, i),
        cond=lambda i: "x > %d" % i, AND=" and ", OR=" or ", ifc=lambda c: "if %s:\n    y = 1" % c,
        for_=lambda i: "for i in range(x):\n    y += i", while_=lambda i: "while y > %d:\n    y -= 1" % (100 + i),
        switch=lambda n: "match x:\n" + "\n".join("    case %d:\n        y = %d" % (i, i) for i in range(1, n + 1)) + "\n    case _:\n        y = 0",
        handler=lambda i: "try:\n    y = int(str(x))\nexcept ValueError:\n    y = %d" % i,
        ternary=lambda i: "y = y + 1 if x > %d else y" % i,
        stmt=lambda i: "y = y + %d" % i,
        special=("comprehension filters (4 comprehensions, each a for and an if)",
                 lambda: "\n".join("v%d = [v for v in range(x) if v > %d]" % (i, i) for i in range(4)) + "\ny = len(v0) + len(v1) + len(v2) + len(v3)"),
        grow=("def classify(score):\n", lambda i: '    if score == %d:\n        return "s%d"\n' % (i, i)),
    )


def braces(fn, if_, for_, while_, switch, handler, ternary, stmt, special, grow, AND=" && ", OR=" || ", ifc=None, cond=None, body_indent=4):
    return dict(w=fn, if_=if_, for_=for_, while_=while_, switch=switch, handler=handler, ternary=ternary, stmt=stmt,
                special=special, grow=grow, AND=AND, OR=OR, ifc=ifc, cond=cond)


def ts():
    return braces(
        fn=lambda name, body: "export function %s(x: number, y: number): number {\n%s\n  return y;\n}" % (name, ind(body, 2)),
        if_=lambda i: "if (x === %d) {\n  y = %d;\n}" % (i, i),
        cond=lambda i: "x > %d" % i, ifc=lambda c: "if (%s) {\n  y = 1;\n}" % c,
        for_=lambda i: "for (let i = 0; i < x; i++) {\n  y += i;\n}", while_=lambda i: "while (y > %d) {\n  y -= 1;\n}" % (100 + i),
        switch=lambda n: "switch (x) {\n" + "\n".join("  case %d:\n    y = %d;\n    break;" % (i, i) for i in range(1, n + 1)) + "\n  default:\n    y = 0;\n}",
        handler=lambda i: "try {\n  y = JSON.parse(String(x));\n} catch {\n  y = %d;\n}" % i,
        ternary=lambda i: "y = x > %d ? y + 1 : y;" % i,
        stmt=lambda i: "y = y + %d;" % i,
        special=("nullish coalescing ?? (8)", lambda: "const m = new Map<number, number>();\n" + "\n".join("y = m.get(%d) ?? y;" % i for i in range(8))),
        grow=("export function classify(score: number): string {\n", lambda i: '  if (score === %d) {\n    return "s%d";\n  }\n' % (i, i)),
    )


def swift():
    return braces(
        fn=lambda name, body: "func %s(_ x: Int, _ yIn: Int) -> Int {\n    var y = yIn\n%s\n    return y\n}" % (name, ind(body, 4)),
        if_=lambda i: "if x == %d {\n    y = %d\n}" % (i, i),
        cond=lambda i: "x > %d" % i, ifc=lambda c: "if %s {\n    y = 1\n}" % c,
        for_=lambda i: "for i in 0..<x {\n    y += i\n}", while_=lambda i: "while y > %d {\n    y -= 1\n}" % (100 + i),
        switch=lambda n: "switch x {\n" + "\n".join("case %d:\n    y = %d" % (i, i) for i in range(1, n + 1)) + "\ndefault:\n    y = 0\n}",
        handler=lambda i: "do {\n    y = try Int(String(x), format: .number)\n} catch {\n    y = %d\n}" % i,
        ternary=lambda i: "y = x > %d ? y + 1 : y" % i,
        stmt=lambda i: "y = y + %d" % i,
        special=("guard statements (8)", lambda: "\n".join("guard x != %d else {\n    return %d\n}" % (i, i) for i in range(8))),
        grow=("func classify(_ score: Int) -> String {\n", lambda i: '    if score == %d {\n        return "s%d"\n    }\n' % (i, i)),
    )


def rust():
    return braces(
        fn=lambda name, body: "pub fn %s(x: i64, y: i64) -> i64 {\n    let mut y = y;\n%s\n    y\n}" % (name, ind(body, 4)),
        if_=lambda i: "if x == %d {\n    y = %d;\n}" % (i, i),
        cond=lambda i: "x > %d" % i, ifc=lambda c: "if %s {\n    y = 1;\n}" % c,
        for_=lambda i: "for i in 0..x {\n    y += i;\n}", while_=lambda i: "while y > %d {\n    y -= 1;\n}" % (100 + i),
        switch=lambda n: "match x {\n" + "\n".join("    %d => y = %d," % (i, i) for i in range(1, n + 1)) + "\n    _ => y = 0,\n}",
        handler=lambda i: "if let Ok(v) = \"%d\".parse::<i64>() {\n    y += v;\n}" % i,
        ternary=lambda i: "y = if x > %d { y + 1 } else { y };" % i,
        stmt=lambda i: "y = y + %d;" % i,
        special=("match arms with guards (8)", lambda: "match x {\n" + "\n".join("    v if v > %d => y += v," % (10 * i) for i in range(8)) + "\n    _ => y = 0,\n}"),
        grow=("pub fn classify(score: i64) -> &'static str {\n", lambda i: '    if score == %d {\n        return "s%d";\n    }\n' % (i, i)),
    )


def go():
    return braces(
        fn=lambda name, body: "func %s(x int, y int) int {\n%s\n\treturn y\n}" % (name[0].upper() + name[1:], tab(body)),
        if_=lambda i: "if x == %d {\n\ty = %d\n}" % (i, i),
        cond=lambda i: "x > %d" % i, ifc=lambda c: "if %s {\n\ty = 1\n}" % c,
        for_=lambda i: "for i := 0; i < x; i++ {\n\ty += i\n}", while_=lambda i: "for y > %d {\n\ty--\n}" % (100 + i),
        switch=lambda n: "switch x {\n" + "\n".join("case %d:\n\ty = %d" % (i, i) for i in range(1, n + 1)) + "\ndefault:\n\ty = 0\n}",
        handler=lambda i: "if v, err := strconv.Atoi(\"%d\"); err == nil {\n\ty += v\n}" % i,
        ternary=lambda i: "if x > %d {\n\ty++\n}" % i,
        stmt=lambda i: "y = y + %d" % i,
        special=("tagless switch (8 conditions)", lambda: "switch {\n" + "\n".join("case x > %d:\n\ty = %d" % (10 * i, i) for i in range(8)) + "\n}"),
        grow=("func Classify(score int) string {\n", lambda i: '\tif score == %d {\n\t\treturn "s%d"\n\t}\n' % (i, i)),
    )


def kotlin():
    return braces(
        fn=lambda name, body: "fun %s(x: Int, yIn: Int): Int {\n    var y = yIn\n%s\n    return y\n}" % (name, ind(body, 4)),
        if_=lambda i: "if (x == %d) {\n    y = %d\n}" % (i, i),
        cond=lambda i: "x > %d" % i, ifc=lambda c: "if (%s) {\n    y = 1\n}" % c,
        for_=lambda i: "for (i in 0 until x) {\n    y += i\n}", while_=lambda i: "while (y > %d) {\n    y -= 1\n}" % (100 + i),
        switch=lambda n: "when (x) {\n" + "\n".join("    %d -> y = %d" % (i, i) for i in range(1, n + 1)) + "\n    else -> y = 0\n}",
        handler=lambda i: "try {\n    y = \"$x\".toInt()\n} catch (e: NumberFormatException) {\n    y = %d\n}" % i,
        ternary=lambda i: "y = if (x > %d) y + 1 else y" % i,
        stmt=lambda i: "y = y + %d" % i,
        special=("when without subject (8 conditions)", lambda: "when {\n" + "\n".join("    x > %d -> y = %d" % (10 * i, i) for i in range(8)) + "\n    else -> y = 0\n}"),
        grow=("fun classify(score: Int): String {\n", lambda i: '    if (score == %d) {\n        return "s%d"\n    }\n' % (i, i)),
    )


def java():
    def fn(name, body):
        return "final class %sHolder {\n    static int %s(int x, int y) {\n%s\n        return y;\n    }\n}" % (name[0].upper() + name[1:], name, ind(body, 8))
    return braces(
        fn=fn,
        if_=lambda i: "if (x == %d) {\n    y = %d;\n}" % (i, i),
        cond=lambda i: "x > %d" % i, ifc=lambda c: "if (%s) {\n    y = 1;\n}" % c,
        for_=lambda i: "for (int i = 0; i < x; i++) {\n    y += i;\n}", while_=lambda i: "while (y > %d) {\n    y -= 1;\n}" % (100 + i),
        switch=lambda n: "switch (x) {\n" + "\n".join("    case %d:\n        y = %d;\n        break;" % (i, i) for i in range(1, n + 1)) + "\n    default:\n        y = 0;\n}",
        handler=lambda i: "try {\n    y = Integer.parseInt(\"\" + x);\n} catch (NumberFormatException e) {\n    y = %d;\n}" % i,
        ternary=lambda i: "y = x > %d ? y + 1 : y;" % i,
        stmt=lambda i: "y = y + %d;" % i,
        special=("switch expression with arrows (8 cases)", lambda: "y = switch (x) {\n" + "\n".join("    case %d -> %d;" % (i, i) for i in range(1, 9)) + "\n    default -> 0;\n};"),
        grow=("    public static String classify(int score) {\n", lambda i: '        if (score == %d) {\n            return "s%d";\n        }\n' % (i, i)),
    )


def ruby():
    return braces(
        fn=lambda name, body: "def %s(x, y)\n%s\n  y\nend" % (name, ind(body, 2)),
        if_=lambda i: "if x == %d\n  y = %d\nend" % (i, i),
        cond=lambda i: "x > %d" % i, ifc=lambda c: "if %s\n  y = 1\nend" % c,
        for_=lambda i: "for i in 0...x\n  y += i\nend", while_=lambda i: "while y > %d\n  y -= 1\nend" % (100 + i),
        switch=lambda n: "case x\n" + "\n".join("when %d\n  y = %d" % (i, i) for i in range(1, n + 1)) + "\nelse\n  y = 0\nend",
        handler=lambda i: "begin\n  y = Integer(x.to_s)\nrescue ArgumentError\n  y = %d\nend" % i,
        ternary=lambda i: "y = x > %d ? y + 1 : y" % i,
        stmt=lambda i: "y = y + %d" % i,
        special=("unless statements (8)", lambda: "\n".join("unless x == %d\n  y += 1\nend" % i for i in range(8))),
        grow=("  def self.classify(score)\n", lambda i: '    return "s%d" if score == %d\n' % (i, i)),
    )


SYNTAX = {"python": py(), "typescript": ts(), "swift": swift(), "rust": rust(), "go": go(), "kotlin": kotlin(),
          "java": java(), "ruby": ruby()}


def complexity_seeds(lang):
    s = SYNTAX[lang]
    f = STATS_FILE[lang]
    out = []
    def add(i, desc, body):
        out.append({"id": "%s-cx-%s" % (lang, i), "desc": desc, "mode": "append", "file": f,
                    "text": s["w"]("seeded%s" % i.replace("-", "").title().replace(" ", ""), body), "expect": "caught"})
    add("ifs", "8 sequential if statements", "\n".join(s["if_"](i) for i in range(1, N + 1)))
    conds = [s["cond"](i) for i in range(N)]
    expr = conds[0]
    for k, c in enumerate(conds[1:]):
        expr += (s["AND"] if k % 2 == 0 else s["OR"]) + c
    add("bool", "one if whose condition has 7 boolean operators", s["ifc"](expr))
    add("loops", "4 loops and 4 ifs", "\n".join([s["for_"](0), s["if_"](1), s["while_"](0), s["if_"](2), s["for_"](1), s["if_"](3), s["while_"](1), s["if_"](4)]))
    add("switch", "a switch/match/when with 8 cases and a default", s["switch"](N))
    add("handlers", "8 exception handlers (rust: if-let on a parse; go: if err == nil)", "\n".join(s["handler"](i) for i in range(N)))
    add("ternary", "8 conditional expressions (go: 8 ifs)", "\n".join(s["ternary"](i) for i in range(N)))
    add("special", s["special"][0], s["special"][1]())
    out.append({"id": "%s-cx-long" % lang, "desc": "a 62-statement straight-line body (cyclomatic 1, past 60 lines)", "mode": "append", "file": f,
                "text": s["w"]("seededLong", "\n".join(s["stmt"](i) for i in range(62))), "expect": "caught"})
    header, line = s["grow"]
    out.append({"id": "%s-cx-grow" % lang, "desc": "the existing classify grown by 7 ifs in place (9 decision points)", "mode": "insert_after",
                "file": f, "anchor": header, "text": "".join(line(i) for i in range(7)), "expect": "caught"})
    return out


# ------------------------------------------------------------------ duplication

SUMMARIZE_START = {"python": "def summarize(", "typescript": "export function summarize(", "swift": "func summarize(",
                   "rust": "pub fn summarize(", "go": "func Summarize(", "kotlin": "fun summarize(",
                   "java": "    public static int[] summarize(", "ruby": "  def self.summarize(", "shell": "summarize() {"}
END_LINE = {"python": None, "typescript": "}", "swift": "}", "rust": "}", "go": "}", "kotlin": "}", "java": "    }",
            "ruby": "  end", "shell": "}"}
LOCALS = ["total", "count", "smallest", "largest", "values", "value", "mean", "result"]
RENAME = {"total": "acc", "count": "seen", "smallest": "lowest", "largest": "highest", "values": "numbers",
          "value": "number", "mean": "average", "result": "packed"}


def summarize_text(lang):
    text = T[lang][STATS_FILE[lang]]
    lines = text.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith(SUMMARIZE_START[lang]))
    end_line = END_LINE[lang]
    for j in range(start + 1, len(lines)):
        if end_line is None:
            if lines[j] and not lines[j].startswith(" "):
                j -= 1
                while not lines[j].strip():
                    j -= 1
                break
        elif lines[j] == end_line:
            break
    return "\n".join(lines[start:j + 1])


def renamed_fn(text, lang, new):
    old = {"go": "Summarize", "shell": "summarize"}.get(lang, "summarize")
    return text.replace(old + "(", new + "(", 1) if lang != "shell" else text.replace("summarize()", new + "()", 1)


def type2(text, lang):
    """Every local identifier renamed (word boundaries; a label/key spelled like a local is renamed too)."""
    out = text
    for a, b in RENAME.items():
        out = re.sub(r"(?<![\w$])%s(?!\w)" % a, b, out)
    return out


def wrap_new_file(lang, body, name):
    """A whole new file holding `body`, with the language's header."""
    head = {"go": "package core\n\n", "kotlin": "package com.acme.core\n\n", "java": "package com.acme.core;\n\nimport java.util.List;\n\nfinal class %s {\n" % name,
            "shell": "#!/usr/bin/env bash\n", "rust": "", "swift": "", "typescript": "", "python": "", "ruby": "module %s\n" % name}[lang]
    tail = {"java": "\n}\n", "ruby": "\nend\n"}.get(lang, "\n")
    return head + body + tail


def wrap_append(lang, body, name):
    """`body` in a form that may be appended to an existing file."""
    if lang == "java":
        return "final class %s {\n%s\n}" % (name, body)
    if lang == "ruby":
        return "module %s\n%s\nend" % (name, body)
    return body


def reindent(lang, body):
    if lang == "python":
        return "if True:\n" + ind(body, 4)
    if lang == "go":
        return tab(body)
    return ind(body, 4)


def first_six(lang, text):
    """A new function holding the first six significant lines of summarize's body, closed."""
    lines = text.split("\n")[1:]
    taken, sig = [], 0
    for l in lines:
        taken.append(l)
        n = " ".join(l.split())
        if len(n) >= 3 and not set(n) <= set("{}()[];,"):
            sig += 1
        if sig == 6:
            break
    opens = sum(l.count("{") for l in taken) - sum(l.count("}") for l in taken)
    if lang == "python":
        return "def head_copy(values):\n" + "\n".join(taken) + "\n    return total"
    if lang == "ruby":
        blocks = sum(1 for l in taken if re.search(r"\bdo\b|^\s*(if|while|for)\b", l))
        return "  def self.head_copy(values)\n" + "\n".join(taken) + "\n" + "    end\n" * blocks + "    total\n  end"
    if lang == "shell":
        dos = sum(1 for l in taken if l.rstrip().endswith("do"))
        return "head_copy() {\n" + "\n".join(taken) + "\n" + "  done\n" * dos + "  echo \"$total\"\n}"
    header = {"typescript": "export function headCopy(values: number[]): number {", "swift": "func headCopy(_ values: [Int]) -> Int {",
              "rust": "pub fn head_copy(values: &[i64]) -> i64 {", "go": "func HeadCopy(values []int) int {",
              "kotlin": "fun headCopy(values: List<Int>): Int {", "java": "    public static int headCopy(List<Integer> values) {"}[lang]
    ret = {"typescript": "  return total;", "swift": "    return total", "rust": "    total", "go": "\treturn total",
           "kotlin": "    return total", "java": "        return total;"}[lang]
    closer = {"java": "    }"}.get(lang, "}")
    inner = "\n".join(taken) + "\n" + "".join("    }\n" for _ in range(opens))
    return header + "\n" + inner + ret + "\n" + closer


def duplication_seeds(lang):
    src = summarize_text(lang)
    stats, model = STATS_FILE[lang], MODEL_FILE[lang]
    newfile = STATS_FILE[lang].rsplit("/", 1)[0] + "/extra" + EXT[lang]
    out = []
    for kind, f in (("t1", lambda t: t), ("t2", lambda t: type2(t, lang))):
        name = "summarizeCopy" if lang not in ("python", "rust", "ruby", "shell") else "summarize_copy"
        if lang == "go":
            name = "SummarizeCopy"
        whole = f(renamed_fn(src, lang, name))
        out.append({"id": "%s-dup-%s-newfile" % (lang, kind), "desc": "summarize copied whole into a new file", "mode": "new",
                    "file": newfile, "text": wrap_new_file(lang, whole, "Extra"), "kind": kind})
        out.append({"id": "%s-dup-%s-samefile" % (lang, kind), "desc": "summarize copied whole into the same file", "mode": "append",
                    "file": stats, "text": wrap_append(lang, whole, "StatsCopy") if lang in ("java", "ruby") else whole, "kind": kind})
        out.append({"id": "%s-dup-%s-reindented" % (lang, kind), "desc": "summarize copied whole, one level deeper", "mode": "append",
                    "file": stats, "text": wrap_append(lang, reindent(lang, whole), "StatsDeep") if lang in ("java", "ruby") else reindent(lang, whole), "kind": kind})
        out.append({"id": "%s-dup-%s-otherlayer" % (lang, kind), "desc": "summarize copied whole into the models file", "mode": "append",
                    "file": model, "text": wrap_append(lang, whole, "ModelCopy") if lang in ("java", "ruby") else whole, "kind": kind})
        six = f(first_six(lang, src))
        out.append({"id": "%s-dup-%s-sixlines" % (lang, kind), "desc": "the first six significant lines of summarize's body, in a new function", "mode": "append",
                    "file": stats, "text": wrap_append(lang, six, "StatsHead") if lang in ("java", "ruby") else six, "kind": kind})
    for s in out:
        s["expect"] = "caught" if s["kind"] == "t1" else "missed"
    return out


# ------------------------------------------------------------------ layering

LAYERING = {
    "python": [("static import", "from services.billing import charge", "caught"),
               ("relative import", "from ..services.billing import charge", "caught"),
               ("importlib.import_module", "import importlib\n\nbilling = importlib.import_module(\"services.billing\")", "missed")],
    "typescript": [("static import", "import { charge } from \"../services/billing\";", "caught"),
                   ("dynamic import()", "export const lazy = () => import(\"../services/billing\");", "caught"),
                   ("type-only import", "import type { charge as Charge } from \"../services/billing\";", "caught")],
    "rust": [("use crate::services", "use crate::services::billing::charge;", "caught"),
             ("fully qualified path, no use", "pub fn total_of(items: &[Item]) -> i64 {\n    crate::services::billing::charge(items).total\n}", "missed")],
    "go": [("import the services package", "import _ \"example.com/acme/src/services\"", "caught"),
           ("dot import", "import . \"example.com/acme/src/services\"", "missed")],
    "kotlin": [("import", "import com.acme.services.Billing", "caught"),
               ("fully qualified name, no import", "fun billIt(): Any = com.acme.services.Billing()", "missed")],
    "java": [("import", "import com.acme.services.Billing;", "caught"),
             ("fully qualified name, no import", "final class Up {\n    Object b = new com.acme.services.Billing();\n}", "missed")],
    "swift": [("a services type named in models", "extension Item {\n    func bill() -> Summary {\n        return BillingService().charge([self])\n    }\n}", "caught"),
              ("a services free function called from models", "extension Item {\n    func billFree() -> Summary {\n        return chargeAll([self])\n    }\n}", "missed")],
    "ruby": [("a services constant named in models", "class Item\n  def bill\n    Billing.charge([self])\n  end\nend", "caught"),
             ("send by string", "class Item\n  def bill_dynamic\n    Object.const_get(\"Billing\").charge([self])\n  end\nend", "missed")],
}
# imports where a reader by imports must see the line where imports go (top of file)
TOP = {"python", "typescript", "rust", "go", "kotlin", "java"}


def layering_seeds(lang):
    out = []
    for i, (desc, text, expect) in enumerate(LAYERING.get(lang, [])):
        mode = "insert_top" if lang in TOP and desc.split()[0] in ("static", "relative", "use", "import", "type-only", "dynamic") else "append"
        out.append({"id": "%s-layer-%d" % (lang, i), "desc": desc, "mode": mode, "file": MODEL_FILE[lang], "text": text, "expect": expect})
    return out


# ------------------------------------------------------------------ dead symbols

DEAD = {
    "python": [("dead function", "def legacy_total(values):\n    return sum(values)"), ("dead class", "class LegacyFormatter:\n    pass"),
               ("dead function, all-lowercase name", "def oldtotal(values):\n    return sum(values)"),
               ("dead function named like a live local", "def prices(values):\n    return list(values)")],
    "typescript": [("dead function", "export function legacyTotal(values: number[]): number {\n  return values.length;\n}"),
                   ("dead class", "export class LegacyFormatter {}"),
                   ("dead function, all-lowercase name", "export function oldtotal(values: number[]): number {\n  return values.length;\n}"),
                   ("dead function named like a live local", "export function prices(values: number[]): number[] {\n  return values;\n}")],
    "swift": [("dead function", "func legacyTotal(_ values: [Int]) -> Int {\n    return values.count\n}"), ("dead type", "final class LegacyFormatter {}"),
              ("dead function, all-lowercase name", "func oldtotal(_ values: [Int]) -> Int {\n    return values.count\n}"),
              ("dead function named like a live local", "func prices(_ values: [Int]) -> [Int] {\n    return values\n}")],
    "rust": [("dead function", "pub fn legacy_total(values: &[i64]) -> i64 {\n    values.len() as i64\n}"), ("dead type", "pub struct LegacyFormatter;"),
             ("dead function, all-lowercase name", "pub fn oldtotal(values: &[i64]) -> i64 {\n    values.len() as i64\n}"),
             ("dead function named like a live local", "pub fn prices(values: &[i64]) -> Vec<i64> {\n    values.to_vec()\n}")],
    "go": [("dead function", "func LegacyTotal(values []int) int {\n\treturn len(values)\n}"), ("dead type", "type LegacyFormatter struct{}"),
           ("dead function, all-lowercase name", "func oldtotal(values []int) int {\n\treturn len(values)\n}"),
           ("dead function named like a live local", "func prices(values []int) []int {\n\treturn values\n}")],
    "kotlin": [("dead function", "fun legacyTotal(values: List<Int>): Int = values.size"), ("dead class", "class LegacyFormatter"),
               ("dead function, all-lowercase name", "fun oldtotal(values: List<Int>): Int = values.size"),
               ("dead function named like a live local", "fun prices(values: List<Int>): List<Int> = values")],
    "java": [("dead method", "final class LegacyA {\n    static int legacyTotal(int[] values) {\n        return values.length;\n    }\n}"),
             ("dead class", "final class LegacyFormatter {}"),
             ("dead method, all-lowercase name", "final class LegacyB {\n    static int oldtotal(int[] values) {\n        return values.length;\n    }\n}"),
             ("dead method named like a live local", "final class LegacyC {\n    static int[] prices(int[] values) {\n        return values;\n    }\n}")],
    "ruby": [("dead method", "def legacy_total(values)\n  values.sum\nend"), ("dead class", "class LegacyFormatter\nend"),
             ("dead method, all-lowercase name", "def oldtotal(values)\n  values.sum\nend"),
             ("dead method named like a live local", "def items(values)\n  values\nend")],
}


def dead_seeds(lang):
    out = []
    for i, (desc, text) in enumerate(DEAD.get(lang, [])):
        expect = "missed" if ("lowercase" in desc or "like a live" in desc) else "caught"
        mode = "append"
        if lang == "java" and "method" in desc:   # a method of the live Stats class, so no dead wrapper class decides it
            text, mode = "\n".join(text.split("\n")[1:-1]), "insert_before_last_brace"
        out.append({"id": "%s-dead-%d" % (lang, i), "desc": desc, "mode": mode, "file": STATS_FILE[lang], "text": text, "expect": expect})
    return out

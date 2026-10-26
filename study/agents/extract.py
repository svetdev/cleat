"""Stage 1: read candidate transcripts and emit, per transcript, an ordered stream of steps:
tool calls (with input and result text), stop-hook firings, and parsed cleat gate reports.
Usage: extract.py CANDIDATES.txt OUT.jsonl   (paths relative to ~/.claude/projects)"""
import json, os, re, sys

ROOT = os.path.expanduser("~/.claude/projects")
CHECK_RE = re.compile(r"quality/bin/(gate\.py|check-([a-z-]+)\.(?:py|sh))")
ITEM_RE = re.compile(r"^\s+(?P<file>[^\s:]+\.[A-Za-z0-9]+)(?::(?P<line>\d+))?\s{2,}(?P<rest>.*)$")


def text_of(c):
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        out = []
        for x in c:
            if isinstance(x, dict):
                if "text" in x:
                    out.append(str(x["text"]))
                elif "content" in x:
                    out.append(text_of(x["content"]))
            else:
                out.append(str(x))
        return "\n".join(out)
    return str(c)


def parse_items(body):
    """FAIL item lines (file[:line]  detail) inside a gate's output, only those under a FAIL
    heading (NOTE sections list improvements, which are not failures)."""
    items, in_fail = [], False
    for ln in body.splitlines():
        s = ln.strip()
        if s.startswith("FAIL"):
            in_fail = True
            continue
        if s.startswith("NOTE") or s.startswith("Tighten") or s.startswith("Split the") or s.startswith("ok"):
            in_fail = False
            continue
        if in_fail:
            m = ITEM_RE.match(ln)
            if m:
                items.append({"file": m.group("file"), "line": int(m.group("line")) if m.group("line") else None,
                              "detail": m.group("rest")[:200]})
    return items


def parse_hook_feedback(txt):
    """{gate: {"status": "FAIL", "items": [...], "body": str}} from a Stop hook feedback message."""
    i = txt.find("quality gate(s) failed")
    if i < 0:
        return None
    body = txt[i:]
    gates = {}
    parts = re.split(r"\n\[([A-Za-z0-9_.-]+)\]\n", "\n" + body.split("\n", 1)[1] if "\n" in body else "")
    # parts: [pre, name1, body1, name2, body2...]
    for k in range(1, len(parts) - 1, 2):
        gates[parts[k]] = {"status": "FAIL", "items": parse_items(parts[k + 1]), "body": parts[k + 1][:4000]}
    return gates


def parse_gate_py(txt):
    """Rows of `gate.py` (non-hook) output: '  FAIL  name' / '  ok    name' / '  skip  name'
    with each gate's output indented 8 spaces beneath."""
    gates, cur = {}, None
    for ln in txt.splitlines():
        m = re.match(r"^  (FAIL|ok  |ERR |skip)  ([A-Za-z0-9_.-]+)", ln)
        if m:
            st = m.group(1).strip()
            cur = m.group(2)
            gates[cur] = {"status": {"ok": "ok", "FAIL": "FAIL", "ERR": "ERR", "skip": "skip"}[st], "body": ""}
            continue
        if cur and ln.startswith("        "):
            gates[cur]["body"] += ln[8:] + "\n"
        elif cur and not ln.startswith("        "):
            cur = None
    for g in gates.values():
        g["items"] = parse_items(g["body"]) if g["status"] == "FAIL" else []
        g["body"] = g["body"][:4000]
    return gates


def parse_check(name, txt, cmd):
    """A check-<name> script run directly: FAIL if a 'FAIL:' line is visible, ok if an
    explicit pass is visible (an echoed exit 0 for it, or no FAIL with output shown)."""
    if "FAIL:" in txt:
        return {name: {"status": "FAIL", "items": parse_items(txt), "body": txt[:4000], "direct": True}}
    m = re.search(r"%s[^\n]{0,20}?(?:exit)?[: =]+(\d+)" % re.escape(name), txt)
    if m and m.group(1) == "0":
        return {name: {"status": "ok", "items": [], "body": txt[:400], "direct": True}}
    if txt.strip() and not re.search(r"\|\s*(grep|head|tail)|>\s*/dev/null", cmd):
        return {name: {"status": "ok", "items": [], "body": txt[:400], "direct": True}}
    return {name: {"status": "unknown", "items": [], "body": txt[:400], "direct": True}}


def stream(path):
    steps, pending = [], {}
    n = 0
    for raw in open(path, errors="replace"):
        try:
            d = json.loads(raw)
        except Exception:
            continue
        t, ts = d.get("type"), d.get("timestamp", "")
        if t == "system" and d.get("subtype") == "stop_hook_summary":
            cmds = [h.get("command", "") for h in d.get("hookInfos", []) or []]
            gcmd = [c for c in cmds if "gate.py" in c]
            if gcmd:
                errs = [e for e in (d.get("hookErrors") or []) if "gate.py" in e or "cleat" in e]
                steps.append({"kind": "stophook", "ts": ts, "cmd": gcmd[0][-160:], "errs": [e[-3000:] for e in errs],
                              "or_true": "|| true" in gcmd[0], "cwd": d.get("cwd", "")})
            continue
        msg = d.get("message") or {}
        c = msg.get("content")
        if t == "user":
            if isinstance(c, str):
                if c.startswith("Stop hook feedback") and "quality gate(s) failed" in c:
                    steps.append({"kind": "report", "src": "hook", "ts": ts, "cwd": d.get("cwd", ""), "gates": parse_hook_feedback(c),
                                  "text": c[:6000]})
                elif "still failing (" in c and "cleat" in c:
                    steps.append({"kind": "report_again", "ts": ts, "text": c[:500]})
                continue
            for x in c or []:
                if not isinstance(x, dict):
                    continue
                if x.get("type") == "tool_result":
                    tid = x.get("tool_use_id")
                    tx = text_of(x.get("content"))
                    if tid in pending:
                        st = pending.pop(tid)
                        st["result"] = tx[:20000]
                        st["is_error"] = bool(x.get("is_error"))
                        cmd = st["input"].get("command", "") if isinstance(st["input"], dict) else ""
                        if st["name"] == "Bash" and CHECK_RE.search(cmd or ""):
                            gates = {}
                            if "gate.py" in cmd and "--guard" not in cmd and "--summary" not in cmd and "--stats" not in cmd:
                                gates.update(parse_gate_py(tx))
                            direct = [m for m in CHECK_RE.finditer(cmd) if m.group(2)]
                            ambiguous = len(direct) > 1 or (direct and "gate.py" in cmd)
                            for m in direct:
                                if ambiguous:
                                    continue  # one output for several checks: not attributable
                                if m.group(2):
                                    nm = m.group(2)
                                    sub = re.search(r"--gate[ =]([\w:-]+)", cmd[m.end():m.end() + 200].split("&&")[0].split(";")[0])
                                    if sub:
                                        nm = "%s:%s" % (nm, sub.group(1))
                                    if nm not in gates:
                                        gates.update(parse_check(nm, tx, cmd))
                            m = re.search(r"gate: (\d+) gate\(s\), (all passed\.|(\d+) failed\.)", tx)
                            if m and "gate.py" in cmd:
                                nfail = 0 if m.group(2).startswith("all") else int(m.group(3))
                                shown = [g for g, v in gates.items() if v["status"] in ("FAIL", "ERR")]
                                if len(shown) == nfail:
                                    gates["__complete__"] = {"status": "complete", "items": [], "body": ""}
                            if gates:
                                steps.append({"kind": "report", "src": "agent", "ts": ts, "cwd": st.get("cwd", ""), "gates": gates,
                                              "tool_idx": st["idx"], "cmd": cmd[:20000]})
                    if "cleat: refused" in tx:
                        steps.append({"kind": "refusal", "ts": ts, "text": tx[:400]})
                elif x.get("type") == "text":
                    tx = x.get("text", "")
                    if tx.startswith("Stop hook feedback") and "quality gate(s) failed" in tx:
                        steps.append({"kind": "report", "src": "hook", "ts": ts, "cwd": d.get("cwd", ""), "gates": parse_hook_feedback(tx),
                                      "text": tx[:6000]})
        elif t == "assistant":
            for x in c or []:
                if isinstance(x, dict) and x.get("type") == "tool_use":
                    st = {"kind": "tool", "idx": n, "ts": ts, "cwd": d.get("cwd", ""), "name": x.get("name"), "input": x.get("input", {}),
                          "id": x.get("id")}
                    n += 1
                    pending[x.get("id")] = st
                    steps.append(st)
    return steps


def main():
    cands, out = sys.argv[1], sys.argv[2]
    with open(out, "w") as fo:
        for rel in open(cands):
            rel = rel.strip()
            if not rel or "opensource-cleat" in rel:
                continue
            p = os.path.join(ROOT, rel)
            steps = stream(p)
            if not any(s["kind"] == "report" and any(g["status"] == "FAIL" for g in s["gates"].values()) for s in steps):
                continue
            fo.write(json.dumps({"file": rel, "steps": steps}) + "\n")


if __name__ == "__main__":
    main()

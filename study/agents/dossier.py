"""Print the window of one event for inspection (raw, NOT anonymized — for checking a
classification by eye): WORK=../q4_work python3 dossier.py EID [full]"""
import json, sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("WORK", os.path.join(HERE, "..", "q4_work"))
eid = int(sys.argv[1]); full = len(sys.argv) > 2
idx = [json.loads(l) for l in open(os.path.join(WORK, "event_index.jsonl"))][eid]
ev = [json.loads(l) for l in open(os.path.join(HERE, "events.jsonl"))][eid]
print(json.dumps({k: ev[k] for k in ("gate", "file", "category", "items", "evidence")}, indent=1)[:3000])
for line in open(os.path.join(WORK, "streams.jsonl")):
    d = json.loads(line)
    if d["file"] != idx["file"]:
        continue
    end = idx["closed_at"] if idx["closed_at"] is not None else len(d["steps"])
    for pos in range(idx["first"], min(end + 1, len(d["steps"]))):
        s = d["steps"][pos]
        if s["kind"] == "tool":
            inp = s["input"] if isinstance(s["input"], dict) else {}
            what = inp.get("command") or inp.get("file_path") or json.dumps(inp)
            n = 4000 if full else 300
            print(pos, "T%d" % s["idx"], s["name"], str(what)[:n].replace("\n", "\n      " if full else "⏎"))
            if s["name"] in ("Edit", "Write") and full:
                print("      OLD:", (inp.get("old_string") or "")[:2500].replace("\n", "\n      "))
                print("      NEW:", (inp.get("new_string") or inp.get("content") or "")[:4000].replace("\n", "\n      "))
            if full and s.get("result"):
                print("      RES:", s["result"][:600].replace("\n", "\n      "))
        elif s["kind"] == "report":
            print(pos, "REPORT", s["src"], {g: v["status"] for g, v in s["gates"].items()})
        elif s["kind"] == "stophook":
            print(pos, "STOPHOOK errs=%d" % len(s["errs"]))
        elif s["kind"] == "refusal":
            print(pos, "REFUSAL", s["text"][:200])

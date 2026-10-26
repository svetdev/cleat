#!/bin/sh
# Re-run the whole classification from the transcripts.
#   WORK  raw, un-anonymized intermediates (default ../q4_work); must hold anon.json:
#         {"names": [...names to scrub...], "projects": [["<transcript dir substring>", "project-A"], ...]}
#   CLEAT the cleat checkout whose escape-pattern table is read (default ~/Documents/opensource/cleat)
# Needs: python3, lizard (LIZARD in classify.py, default /opt/homebrew/bin/lizard), and
# ripgrep, or GNU/BSD grep as the fallback below.
set -e
if command -v rg >/dev/null 2>&1; then
  RG() { rg -l "$@" --glob '*.jsonl' "$P"; }
else
  RG() { if [ "$1" = -F ]; then shift; grep -rlF --include='*.jsonl' -e "$1" "$P"; else grep -rlE --include='*.jsonl' -e "$1" "$P"; fi; }
fi
OUT=$(cd "$(dirname "$0")" && pwd)
WORK=${WORK:-$OUT/../q4_work}; export WORK
P=~/.claude/projects
# 1. transcripts outside the cleat repo with a cleat Stop-hook failure or a gate FAIL row
#    (subagent transcripts included; worktree dirs are the same project)
{
  RG -F 'quality gate(s) failed'
  RG '  FAIL  [a-z-]+\\n'
  RG 'FAIL  (complexity|escapes|crap|duplication|doc-size|doc-citations|test-hygiene|exclusions|conventions|dead-symbols|guard-suites|layering|reachability|public-api|manifests|inventory|sarif|changed-coverage)'
} | grep -v opensource-cleat | sed "s#^$P/#./#" | sort -u > "$WORK/candidates.txt"
# 2. the projects' own cleat event logs, which tell an exit-0 Stop hook's pass from its
#    "still failing, not blocking again"
find ~/Documents -name .events.jsonl -path '*quality*' 2>/dev/null | grep -v opensource/cleat > "$WORK/_eventlogs.txt"
cd "$P"
python3 "$OUT/extract.py" "$WORK/candidates.txt" "$WORK/streams.jsonl"
python3 "$OUT/classify.py" "$WORK/streams.jsonl" "$OUT" "$WORK"
python3 "$OUT/summarize.py" "$OUT" > /dev/null
python3 "$OUT/examples.py" "$OUT" > /dev/null
echo "wrote $OUT/events.jsonl, summary_tables.md, examples.md"

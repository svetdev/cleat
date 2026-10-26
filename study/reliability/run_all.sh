#!/usr/bin/env bash
# Re-run the whole Q3 reliability study. The scripts live beside this file; everything they
# read and write lives under $STUDY_WORK: the bare clones in work/clones (read-only), venv
# (python 3.14, lizard 1.24.1, radon, numpy, black, pytest, tornado, typeguard), and q3/ —
# venv1240 (lizard 1.24.0), node/ (eslint 9, @typescript-eslint/parser, @ast-grep/cli), data, tmp.
# Needs swiftlint on the PATH.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
: "${STUDY_WORK:?set STUDY_WORK to the scratch directory of the study}"
export STUDY_WORK
Q3="$STUDY_WORK/q3"
PY="$STUDY_WORK/venv/bin/python"
mkdir -p "$Q3/node" && cd "$Q3"
[ -x venv1240/bin/lizard ] || { "$PY" -m venv venv1240 && venv1240/bin/pip install -q lizard==1.24.0; }
[ -x node/node_modules/.bin/eslint ] || (cd node && npm install --silent eslint@9 @typescript-eslint/parser typescript @ast-grep/cli)
# the attach config: attach.py --into a copy of one sampled tree (data/attach-quality.json is the result)
"$PY" "$HERE"/prepare.py                       # sample -> data/orig, data/manifest.json
"$PY" "$HERE"/transforms.py                    # rename, reorder, unparse, black
for v in orig rename reorder unparse black; do "$PY" "$HERE"/measure.py "$v"; done
"$PY" "$HERE"/measure.py orig --lizard-bin "$Q3/venv1240/bin" --as orig_lizard1240 --only-cx
"$PY" "$HERE"/extract.py
"$PY" "$HERE"/measure.py extract1 --only-cx
"$PY" "$HERE"/measure.py extractN --only-cx
"$PY" "$HERE"/analyze_q1.py
"$PY" "$HERE"/verify_gate.py                   # the real gates on 4 clones x 3 variants
"$PY" "$HERE"/gate_variants.py "$HERE/../.." current   # the real gates on every clone x 4 variants
mkdir -p tmp/tests
for c in pallets__click jd__tenacity; do mkdir -p "tmp/tests/$c"; git -C "$STUDY_WORK/work/clones/$c" archive HEAD tests pyproject.toml | tar -x -C "tmp/tests/$c"; done
"$PY" "$HERE"/test_semantics.py
# seeded defects (the corpus is frozen in corpus/FROZEN.json; gen_corpus.py rewrites it)
"$PY" "$HERE"/seed_run.py
# instrument agreement: shallow clones at the recorded commits (results.json), deleted afterwards
git clone -q --depth 1 https://github.com/Alamofire/Alamofire.git tmp/alamofire
git clone -q --depth 1 https://github.com/kean/Nuke.git tmp/nuke
git clone -q --depth 1 https://github.com/colinhacks/zod.git tmp/zod
"$PY" "$HERE"/instruments.py
"$PY" "$HERE"/lizard_versions_other.py
"$PY" "$HERE"/make_results.py
rm -rf tmp/alamofire tmp/nuke tmp/zod node/tsrc

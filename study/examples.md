# An example to run, per language

One small public repository per language, with the commands and what they printed here. None starts an agent or spends a token; each takes under a minute on a laptop. Run one before trying your own repository: if your numbers match, the tools are set up.

Needs Python 3.9 or later, git, and `pip install lizard`. Run every command from the cleat checkout.

| language | repository | commit |
|---|---|---|
| Python | `python-attrs/attrs` | `af29c2a4a6` |
| TypeScript | `pmndrs/zustand` | `5d66d53be8` |
| Rust | `sharkdp/fd` | `c74413f501` |
| Swift | `kean/Nuke` | `c240552c97` |
| Kotlin | `square/okio` | `f0e3f479f8` |

## The commands

With the Python row; for another language change the two names and the commit.

```
mkdir -p ~/study/clones ~/study/trees
git clone https://github.com/python-attrs/attrs ~/study/clones/python-attrs__attrs
```

**What the gates would have refused** in the 20 merged commits up to that one:

```
python3 study/replay/history.py ~/study/clones/python-attrs__attrs ~/study/history 20 6 af29c2a4a6
python3 study/gates/refusals.py ~/study/history
```

**Where the code stands**, on a tree at that commit with cleat attached:

```
git clone ~/study/clones/python-attrs__attrs ~/study/trees/attrs
git -C ~/study/trees/attrs checkout -q af29c2a4a6
python3 quality/bin/attach.py --into ~/study/trees/attrs --no-tests
(cd ~/study/trees/attrs && python3 quality/bin/gate.py && python3 quality/bin/gate.py --summary)
```

**How wide a change is**, and how far one part reaches when a pattern is given:

```
python3 study/gates/reach.py ~/study/clones/python-attrs__attrs .py
```

## What they printed here

Measured 2026-11-27. The gates were green straight after attach on all five.

| | commits replayed | refused | by gate | functions over the ceiling | duplicated lines | escape sites |
|---|---|---|---|---|---|---|
| Python | 20 | 3 | escapes 2, duplication 1 | 21 | 5.04% | 31 |
| TypeScript | 18 | 3 | escapes 3, complexity 1 | 3 | 2.1% | 70 |
| Rust | 20 | 4 | complexity 3, escapes 2 | 15 | 1% | 24 |
| Swift | 20 | 1 | test-hygiene 1 | 30 | 2.04% | 108 |
| Kotlin | 19 | 1 | complexity 1 | 25 | 8.19% | 218 |

Fewer than 20 commits replayed means some of the last 20 did not change a source file the gates read.

A refused commit is one a maintainer merged and a gate would have stopped. Open `~/study/history/<repository>.json` and read the `said` field of each: it holds the gate's own lines, and reading them is how to tell real debt from a false refusal.

## Next

`study/README.md`, under "On your own repository", has the rest in order of cost: whether refused commits are the ones later fixed, whether escapes sit where the bugs were, and the runs that start an agent.

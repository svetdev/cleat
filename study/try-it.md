# Try it on your codebase

Paste one of the prompts below to a coding agent (Claude Code, Cursor, Codex or another) opened in your repository. It measures where the code stands and what cleat's gates would have said about your last 60 merged commits. It takes a few minutes, starts no other agent and costs nothing beyond that one session.

At the end it writes a card you can contribute. It holds the language, rough size, age and number of commits as ranges, test lines against production lines, the measures per 1,000 lines, how many recent commits the gates would have refused, and your answers to two questions. It holds no repository name, path, file name, commit text or code. Sharing it is your choice and a separate step you do by hand.

What it does on your machine, all of it visible in the prompt:

- clones cleat and a second copy of your repository into a new folder beside it, so your working checkout is not touched, and offers to delete that folder at the end;
- installs `lizard` from PyPI if it is missing, after asking;
- runs Python scripts from the cleat checkout. They read your repository through git and send nothing anywhere.

## The short prompt

```
If ../cleat-try already exists, ask me before deleting it. Then clone
https://github.com/svetdev/cleat into ../cleat-try/cleat and follow the prompt in its
study/try-it.md on this repository. Work only inside ../cleat-try, do not change this
repository, do not push or send anything anywhere, and ask me before installing anything.
```

It has the agent read the full prompt below from the clone, so it always runs the current one.

## The full prompt

```
Measure this repository with cleat (https://github.com/svetdev/cleat) and show me the results.
Work only in a new folder ../cleat-try beside this repository. Do not change this repository,
do not push anything, do not open issues or pull requests, and send nothing about this code to
any outside service.

0. If ../cleat-try already exists and you did not just create it, it is left over from an earlier
   run. Ask me before deleting it, and do not go on while it is there.
1. Check for python3 (3.9 or later) and git. If `lizard` is missing, ask me before running
   `pip install lizard`.
2. Set up:
     mkdir -p ../cleat-try/clones ../cleat-try/history
     git clone https://github.com/svetdev/cleat ../cleat-try/cleat     (skip if you already cloned it)
     git clone . ../cleat-try/clones/my__repo
     git clone . ../cleat-try/tree
3. If ../cleat-try/tree has a quality.json, this repository already uses cleat. Tell me, then
   delete quality/ and quality.json in ../cleat-try/tree only, so it is measured fresh.
   Where the code stands:
     python3 ../cleat-try/cleat/quality/bin/attach.py --into ../cleat-try/tree --no-tests
     (cd ../cleat-try/tree && python3 quality/bin/gate.py; python3 quality/bin/gate.py --summary)
4. What the gates would have refused in the last 60 merged commits:
     python3 ../cleat-try/cleat/study/replay/history.py ../cleat-try/clones/my__repo ../cleat-try/history 60 6
     python3 ../cleat-try/cleat/study/gates/refusals.py ../cleat-try/history
5. Ask me two questions and wait for my answers:
     a. When you add a feature to this codebase, how often does something that worked break:
        rarely (easy), sometimes (mixed) or often (breaks)?
     b. Was most, some or none of this code written by an AI agent?
6. The card, with my answers. Each must be exactly one of the words easy|mixed|breaks and
   most|some|none; if what I said does not fit one, ask me which is closest.
     python3 ../cleat-try/cleat/study/replay/share.py state ../cleat-try/tree ../cleat-try/history/my__repo.json --feels <a> --ai <b>

Then tell me, in plain words:
- what attach found and what it left out, and whether the gates passed straight after attach;
- how this codebase compares with the open-source sample in the summary (the pNN figures);
- how many of the 60 commits a gate would have refused, and by which gate;
- for ten of the refused commits, read the `said` field in ../cleat-try/history/my__repo.json
  and say whether each looks like real debt, a false refusal, or generated or vendored code
  that should not have been judged;
- anything that failed or looked wrong. Do not edit cleat's scripts to make a step pass;
  describe what happened instead.

Last, print the card, and ask me whether to open the sharing link in my browser. Only if I say
yes, run the `share.py open` command the last step printed. Do not share the card any other way.

When I say I am done with the card, ask me whether to delete ../cleat-try, and delete it only
if I say yes. It holds a copy of this repository, so it should not be left behind.
```

## Sharing what you got

The last command prints the card and a link. Read the card; it is everything that would be shared. Then pick whichever is easiest:

- **Open the link and press Submit.** It opens a new issue on cleat with the card already filled in. Nothing is sent until you press the button.
- **Paste the card as a comment** wherever you found this prompt.
- **Open a pull request** with the file `../cleat-try/cleat/study/contributed/<id>.json`.

The first and third show your GitHub account; use another if that matters. A card's optional note goes in public, so it should name no company, client or person.

If a step failed or a refusal looked wrong, that is worth more than the card. Open an issue saying which gate, which language, and a few lines that reproduce it, with nothing from your code that you would not publish.

## With and without cleat

The longer study has an agent rebuild a stretch of your real commits twice, once with cleat's gates and once without, and compares what each leaves. It needs a test suite that runs offline, an adapter file for your stack, and about $40 to $60 of agent usage for a first look at 20 changes. `replay/README.md` is its run order, and its results share the same way.

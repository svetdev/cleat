#!/usr/bin/env python3
"""oracle — show that the hidden tests can be passed, and that they are not passed by nothing.

  oracle.py <work dir>

Before any agent runs, three trees are built from seed/ under <work dir>/proof and the
hidden tests of every feature run against each:

  reference   reference/src, an implementation of all of spec.json: every feature must pass
  empty       the seed alone, as a chain starts: every feature must fail
  hollow      a server that answers 200 {} to everything: every feature must fail

and every call each test made is held against spec.json: a feature's test may use only
routes that its own or an earlier feature's request names, or it could not pass at its
own step. What this does not show is that each test passes on an app built only up to
its feature: the reference is the finished app. A test that leans on a later feature
would show as a feature that never passes at its own step in any chain, and `regressed`
in grow.py counts only features that passed before.

Writes <work dir>/proof.json and exits 0 only when all four hold.
"""

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import grow     # noqa: E402
import hidden   # noqa: E402

HOLLOW = """import { serve } from "@hono/node-server";
import { Hono } from "hono";
const app = new Hono();
app.all("*", (c) => c.json({}));
serve({ fetch: app.fetch, port: Number(process.env.PORT ?? 3000) });
"""


def tree(work, name, fill):
    root = os.path.join(work, "proof", name)
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.dirname(root), exist_ok=True)
    grow.new_tree(work, root)
    fill(root)
    return root


def reference(root):
    shutil.copytree(os.path.join(HERE, "reference", "src"), os.path.join(root, "src"))


def hollow(root):
    os.makedirs(os.path.join(root, "src"))
    with open(os.path.join(root, "src", "server.ts"), "w") as handle:
        handle.write(HOLLOW)


def route_of(method, path, routes):
    """The route of spec.json a call is to: same method, same number of segments, the most literal match."""
    parts, best = path.strip("/").split("/"), None
    for route in routes:
        verb, _, shape = route.partition(" ")
        wanted = shape.strip("/").split("/")
        if verb != method or len(wanted) != len(parts) or not all(w.startswith(":") or w == p for w, p in zip(wanted, parts)):
            continue
        literal = sum(not w.startswith(":") for w in wanted)
        if best is None or literal > best[0]:
            best = (literal, route)
    return best and best[1]


def early_calls(results):
    """Calls a feature's test made to a route no request up to that feature names."""
    first, wrong = {}, []
    for feature in grow.features():
        for route in feature["routes"]:
            first.setdefault(route, feature["id"])
    for number, result in sorted(results.items()):
        for method, path in sorted(set(result["calls"])):
            route = route_of(method, path, first)
            if route is None or first[route] > number:
                wrong.append("feature %d calls %s %s (%s)" % (number, method, path, "named by no request" if route is None else "first named by feature %d" % first[route]))
    return wrong


def main(work):
    work = os.path.abspath(work)
    passed, results = {}, {}
    for name, fill in (("reference", reference), ("empty", lambda root: None), ("hollow", hollow)):
        results[name] = hidden.run(tree(work, name, fill))
        passed[name] = sorted(n for n, r in results[name].items() if r["ok"])
        print("%-9s passes %d of %d" % (name, len(passed[name]), len(results[name])))
        for number, result in sorted(results[name].items()):
            if name == "reference" and not result["ok"]:
                print("  %2d  %s" % (number, result["said"][:300]))
    wrong = early_calls(results["reference"])
    print("calls to routes not yet asked for: %d" % len(wrong))
    for line in wrong:
        print("  " + line)
    total = len(results["reference"])
    holds = len(passed["reference"]) == total == len(grow.features()) and not passed["empty"] and not passed["hollow"] and not wrong
    with open(os.path.join(work, "proof.json"), "w") as handle:
        json.dump(dict(holds=holds, features=total, passed=passed, early_calls=wrong,
                       failures={name: {n: r["said"][:300] for n, r in found.items() if not r["ok"]} for name, found in results.items()}), handle, indent=1)
    print("proof holds" if holds else "PROOF FAILS")
    return 0 if holds else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

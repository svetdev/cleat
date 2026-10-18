"""reference — where a part's debt stands against open-source code in its language.

`quality/reference.json` holds, per language, the distribution of each debt
measure (per 1k production lines) across the parts of a sample of open-source
repositories, measured by cleat itself: attach, then the baselines it writes on
day one. `build-reference.py` makes it and records what made it — the repositories,
lizard's version, the date — so anyone can measure it again.

`standing(language, metric, value)` is the share of reference parts in that
language with less of it: 0.7 means more debt per 1k lines than 70% of them. It
is a comparison, not a grade, and only as wide as the sample — the table says
how many parts each language has, and the summary prints it.
"""

import bisect
import json
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reference.json")
METRICS = ("complexity_per_kloc", "over_cc_per_kloc", "escapes_per_kloc", "duplication_percent")
MIN_PARTS = 5   # fewer reference parts than this in a language, and it says nothing


def load(path=PATH):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def standing(table, language, metric, value):
    """(share of reference parts with less, how many parts) — None without enough of them."""
    values = ((table or {}).get("languages", {}).get(language) or {}).get(metric)
    if value is None or not values or len(values) < MIN_PARTS:
        return None
    below = bisect.bisect_left(values, value)
    equal = bisect.bisect_right(values, value) - below
    return round((below + equal / 2.0) / len(values), 2), len(values)


def standings(table, part):
    """{metric: standing} for a part — none for one smaller than the reference's own parts,
    which a sample of larger code says nothing about."""
    smallest = ((table or {}).get("provenance") or {}).get("min_part_lines", 0)
    if part.get("lines", 0) < smallest:
        return {}
    return {k: standing(table, part.get("language"), k, part.get(k)) for k in METRICS if k in part}

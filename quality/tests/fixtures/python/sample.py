"""Conformance fixture: known escapes and one function of known complexity."""
import json  # noqa: F401

x = json.loads("1")  # type: ignore
y = x  # type: ignore[assignment]
# a comment merely mentioning the words type and ignore is not a site


def branchy(a):
    """cyclomatic 5: four ifs and the fall-through."""
    if a == 1:
        return 1
    if a == 2:
        return 2
    if a == 3:
        return 3
    if a == 4:
        return 4
    return 0


def swallow():
    try:
        pass
    except:
        pass

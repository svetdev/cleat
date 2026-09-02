"""wordcount — how many whitespace-separated words a document holds."""


def words(path):
    with open(path, errors="replace") as handle:
        return len(handle.read().split())

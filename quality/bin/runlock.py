"""runlock — who is running the gates right now, so nothing replaces them underneath.

`attach.py --refresh` deletes and rewrites `quality/bin` and `quality/tests`. A
gate or a suite running from that copy at that moment reads half a file.
Runners register under `quality/.running/<pid>` for their lifetime (`held()`),
and `--refresh` refuses while any registered process is alive — a pid file
whose process is gone is stale and cleared. Nothing here locks runners
against each other; several may run at once.
"""

import atexit
import contextlib
import os

DIRNAME = ".running"


def _dir(quality_dir):
    return os.path.join(quality_dir, DIRNAME)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def register(quality_dir, label):
    """Record this process as running under `quality_dir`; returns the file to remove."""
    directory = _dir(quality_dir)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, str(os.getpid()))
    with open(path, "w") as handle:
        handle.write("%s\n" % label)
    return path


def release(path):
    try:
        os.remove(path)
    except OSError:
        pass


@contextlib.contextmanager
def held(quality_dir, label):
    path = register(quality_dir, label)
    atexit.register(release, path)
    try:
        yield
    finally:
        release(path)


def active(quality_dir):
    """[(pid, label)] for the registered processes still alive; stale files are removed."""
    directory = _dir(quality_dir)
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        try:
            pid = int(name)
        except ValueError:
            continue
        if _alive(pid):
            with open(path, errors="replace") as handle:
                out.append((pid, handle.read().strip()))
        else:
            release(path)
    return out

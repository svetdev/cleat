#!/usr/bin/env python3
"""app — start the app in a tree as a person would, and talk to it over HTTP.

The contract the first request states, and the only thing the hidden tests rely on:

  npm start          starts the server, from the tree's root
  PORT               the port it listens on
  DB_FILE            the SQLite file that holds its data

`App(tree)` starts it on a free port with a new, empty database file outside the tree,
and waits until the port answers. `restart()` stops it and starts it again on the same
file, which is how a test asks whether data is kept. `stop()` ends it and removes the
file. The server runs under sandbox-exec with the network denied except for localhost:
it is code an agent wrote.

  app.py <tree>      start the tree's app, print its address, stop on Enter
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

PROFILE = ('(version 1)(allow default)(deny network*)'
           '(allow network* (local ip "localhost:*"))(allow network* (remote ip "localhost:*"))')
START_SECONDS = 20
CALL_SECONDS = 10
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never through a proxy the environment names


class Down(Exception):
    """The app did not start, or stopped answering."""


class Reply:
    def __init__(self, status, raw, headers):
        self.status, self.raw, self.headers = status, raw, headers
        try:
            self.json = json.loads(raw) if raw else None
        except ValueError:
            self.json = None

    def __repr__(self):
        return "%d %s" % (self.status, self.raw[:200])


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def clean_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    return env


class App:
    def __init__(self, tree):
        self.tree = tree
        self.dir = tempfile.mkdtemp(prefix="regress-db-")
        self.port = None
        self.process = None
        self.start()

    def start(self):
        self.port = free_port()
        env = dict(clean_env(), PORT=str(self.port), DB_FILE=os.path.join(self.dir, "app.db"), NO_COLOR="1")
        self.log = open(os.path.join(self.dir, "server.log"), "a")
        self.process = subprocess.Popen(["sandbox-exec", "-p", PROFILE, "npm", "start"], cwd=self.tree, env=env,
                                        stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.time() + START_SECONDS
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise Down("`npm start` exited with %s: %s" % (self.process.returncode, self.said()))
            with socket.socket() as probe:
                probe.settimeout(0.3)
                if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                    return
            time.sleep(0.05)
        self.halt()
        raise Down("nothing listened on PORT within %ds of `npm start`: %s" % (START_SECONDS, self.said()))

    def said(self):
        with open(os.path.join(self.dir, "server.log"), errors="replace") as handle:
            return handle.read()[-400:].strip()

    def halt(self):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)   # npm and the server it started
                self.process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if self.process:
            self.log.close()

    def restart(self):
        self.halt()
        self.start()

    def stop(self):
        self.halt()
        shutil.rmtree(self.dir, ignore_errors=True)

    def call(self, method, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        sent = dict({"Content-Type": "application/json"} if data is not None else {}, **(headers or {}))
        request = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, method=method, headers=sent)
        try:
            with DIRECT.open(request, timeout=CALL_SECONDS) as answer:
                return Reply(answer.status, answer.read().decode(errors="replace"), answer.headers)
        except urllib.error.HTTPError as error:
            return Reply(error.code, error.read().decode(errors="replace"), error.headers)
        except (urllib.error.URLError, OSError) as error:
            raise Down("%s %s got no answer: %s" % (method, path, error))


if __name__ == "__main__":
    app = App(os.path.abspath(sys.argv[1]))
    print("http://127.0.0.1:%d  (database and log in %s)" % (app.port, app.dir))
    try:
        input("Enter stops it. ")
    finally:
        app.stop()

"""A forked child must not share the parent's HTTP connections (issue #123).

The resource monitor runs in a child process forked by ``Task.init``. The child inherits the parent's pooled keep-alive
sockets, so parent and child read each other's responses. The session gives each process a connection pool of its own.
"""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pyhocon import ConfigFactory

from cairnml.backend_api.session.session import Session


class _EchoEndpoint(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        body = ('{"meta": {"endpoint": {"name": "%s"}}}' % self.path.strip("/")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def echo_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EchoEndpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _session_with_pool() -> Session:
    session = Session.__new__(Session)
    session._Session__http_retries_config = {"total": 0, "config": ConfigFactory.from_dict({})}
    session._Session__http_session = session._Session__new_http_session()
    return session


def test_forked_child_gets_a_connection_pool_of_its_own(monkeypatch):
    session = _session_with_pool()
    parent_pool = session._Session__http_session
    assert session._Session__http_session_of_this_process() is parent_pool

    child_pid = os.getpid() + 1
    monkeypatch.setattr(os, "getpid", lambda: child_pid)

    child_pool = session._Session__http_session_of_this_process()
    assert child_pool is not parent_pool
    assert session._Session__http_session_of_this_process() is child_pool


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
def test_parent_and_forked_child_each_get_their_own_responses(echo_server):
    session = _session_with_pool()
    # One pooled keep-alive connection exists before the fork, as it does when Task.init forks the monitor.
    assert session._Session__http_session_of_this_process().get(f"{echo_server}/warm", timeout=5).ok

    pid = os.fork()
    if pid == 0:
        status = 1
        try:
            wrong = 0
            for i in range(50):
                reply = session._Session__http_session_of_this_process().get(f"{echo_server}/child.{i}", timeout=5)
                wrong += reply.json()["meta"]["endpoint"]["name"] != f"child.{i}"
            status = 0 if wrong == 0 else 2
        finally:
            os._exit(status)

    wrong = 0
    for i in range(50):
        body = session._Session__http_session_of_this_process().get(f"{echo_server}/parent.{i}", timeout=5).json()
        wrong += body["meta"]["endpoint"]["name"] != f"parent.{i}"
    _, child_status = os.waitpid(pid, 0)
    assert wrong == 0, f"{wrong} of 50 parent responses belong to another request"
    assert os.waitstatus_to_exitcode(child_status) == 0, "the child received another request's response"

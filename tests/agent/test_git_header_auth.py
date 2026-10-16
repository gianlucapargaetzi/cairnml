import base64
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from clearml_agent.helper.repo import Git, fetch_git_token, github_header, scrub_token

TOKEN = "ghs_exampletoken123"


def basic(token):
    return "basic " + base64.b64encode("x-access-token:{}".format(token).encode()).decode()


def test_the_header_carries_the_installation_token_as_x_access_token():
    assert github_header(TOKEN) == "AUTHORIZATION: basic " + base64.b64encode(
        "x-access-token:{}".format(TOKEN).encode()).decode()


def test_scrubbing_removes_the_token_in_every_form_from_text_and_bytes():
    raw = base64.b64encode("x-access-token:{}".format(TOKEN).encode()).decode()
    text = "fatal: {} {} {}".format(github_header(TOKEN), raw, TOKEN)
    cleaned = scrub_token(text, TOKEN)
    assert TOKEN not in cleaned and raw not in cleaned and "AUTHORIZATION" not in cleaned
    assert scrub_token(text.encode(), TOKEN).find(TOKEN.encode()) == -1


def test_no_token_means_nothing_is_scrubbed():
    assert scrub_token("nothing here", None) == "nothing here"


def test_a_header_token_is_only_in_the_environment_of_git():
    vcs = Git.__new__(Git)
    vcs.COMMAND_ENV = dict(Git.COMMAND_ENV)
    vcs._header_token = None
    vcs.use_header_token(TOKEN)
    assert vcs.COMMAND_ENV["GIT_CONFIG_COUNT"] == "1"
    assert vcs.COMMAND_ENV["GIT_CONFIG_VALUE_0"] == github_header(TOKEN)
    assert "GIT_ASKPASS" not in vcs.COMMAND_ENV
    assert TOKEN not in Git.COMMAND_ENV.values()


def test_git_output_with_the_token_is_scrubbed_by_the_repository():
    vcs = Git.__new__(Git)
    vcs.url = "https://github.com/acme/repo.git"
    vcs._header_token = TOKEN
    assert TOKEN not in vcs._normalize_output("error: {} {}".format(github_header(TOKEN), TOKEN))


def test_fetching_the_token_asks_the_server_for_the_tasks_repository():
    class Reply:
        ok = True

        def json(self):
            return {"data": {"token": TOKEN}}

    session = SimpleNamespace(send_request=lambda **kwargs: Reply())
    assert fetch_git_token(session, "task-1") == TOKEN


def test_a_missing_token_is_none_so_the_configured_credentials_are_used():
    class Reply:
        ok = False

    session = SimpleNamespace(send_request=lambda **kwargs: Reply())
    assert fetch_git_token(session, "task-1") is None


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="git is not installed")
def test_real_git_sends_the_header_and_never_the_token_in_the_url(tmp_path):
    seen = []

    class Recorder(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("Authorization"))
            self.send_response(401)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=server.handle_request, daemon=True).start()
    url = "http://127.0.0.1:{}/acme/repo.git".format(server.server_address[1])

    vcs = Git.__new__(Git)
    vcs.COMMAND_ENV = dict(Git.COMMAND_ENV)
    vcs._header_token = None
    vcs.use_header_token(TOKEN)
    env = {**os.environ, **vcs.COMMAND_ENV, "GIT_CONFIG_GLOBAL": os.devnull}
    subprocess.run(["git", "ls-remote", url], env=env, capture_output=True)
    server.server_close()

    assert seen and seen[0] == basic(TOKEN)
    assert TOKEN not in url

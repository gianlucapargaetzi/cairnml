import base64
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from cairnml_agent.helper.repo import (
    Git, basic_auth_header, fetch_git_credential, github_header, scrub_token, select_git_credential,
    wants_stored_credential,
)

TOKEN = "glpat-exampletoken123"


class Reply:
    def __init__(self, ok=True, data=None):
        self.ok = ok
        self._data = data

    def json(self):
        return {"data": self._data}


def session_answering(reply):
    """A session stub that records each server request and answers with reply."""
    calls = []

    def send_request(**kwargs):
        calls.append(kwargs)
        return reply

    return SimpleNamespace(send_request=send_request), calls


def test_a_basic_header_carries_the_stored_username_and_secret():
    raw = base64.b64encode("oauth2:{}".format(TOKEN).encode()).decode()
    assert basic_auth_header("oauth2", TOKEN) == "AUTHORIZATION: basic " + raw


def test_the_github_header_is_the_basic_header_with_the_app_username():
    assert github_header(TOKEN) == basic_auth_header("x-access-token", TOKEN)


def test_scrubbing_removes_a_stored_credential_in_every_form():
    raw = base64.b64encode("deploy-bot:{}".format(TOKEN).encode()).decode()
    text = "fatal: {} {} {}".format(basic_auth_header("deploy-bot", TOKEN), raw, TOKEN)
    cleaned = scrub_token(text, TOKEN, "deploy-bot")
    assert TOKEN not in cleaned and raw not in cleaned and "AUTHORIZATION" not in cleaned


def test_the_repository_scrubs_the_stored_credential_it_was_given():
    vcs = Git.__new__(Git)
    vcs.url = "https://gitlab.example.com/team/repo.git"
    vcs._header_token = TOKEN
    vcs._header_username = "deploy-bot"
    message = "error: {} {}".format(basic_auth_header("deploy-bot", TOKEN), TOKEN)
    assert TOKEN not in vcs._normalize_output(message)


def test_a_header_token_with_a_username_is_only_in_the_environment_of_git():
    vcs = Git.__new__(Git)
    vcs.COMMAND_ENV = dict(Git.COMMAND_ENV)
    vcs._header_token = None
    vcs.use_header_token(TOKEN, username="oauth2")
    assert vcs.COMMAND_ENV["GIT_CONFIG_VALUE_0"] == basic_auth_header("oauth2", TOKEN)
    assert TOKEN not in Git.COMMAND_ENV.values()


@pytest.mark.parametrize("url, wanted", [
    ("https://github.com/acme/repo.git", True),
    ("https://gitlab.example.com/team/repo.git", True),
    ("https://bitbucket.org/team/repo", True),
    ("https://gitlab.example.com:8443/team/repo.git", True),
    ("http://gitlab.example.com/team/repo.git", False),
    ("ssh://git@gitlab.example.com/team/repo.git", False),
    ("git@gitlab.example.com:team/repo.git", False),
    ("gitlab.example.com/team/repo.git", False),
    ("", False),
    (None, False),
])
def test_only_https_repositories_and_github_ask_the_server_for_a_credential(url, wanted):
    assert wants_stored_credential(url) is wanted


def test_a_non_github_https_repository_gets_the_stored_credential_of_the_task():
    session, calls = session_answering(Reply(data={"token": TOKEN, "username": "oauth2"}))
    credential = select_git_credential(session, "https://gitlab.example.com/team/repo.git", "task-1")
    assert credential == ("oauth2", TOKEN)
    assert calls == [dict(service="devices", action="git_token", version="2.8",
                          method=calls[0]["method"], json={"task": "task-1"})]


def test_the_credential_is_never_asked_for_a_plain_http_or_ssh_repository():
    session, calls = session_answering(Reply(data={"token": TOKEN, "username": "oauth2"}))
    assert select_git_credential(session, "http://gitlab.example.com/team/repo.git", "task-1") is None
    assert select_git_credential(session, "ssh://git@gitlab.example.com/team/repo.git", "task-1") is None
    assert calls == []


def test_without_a_task_no_credential_is_asked_for():
    session, calls = session_answering(Reply(data={"token": TOKEN, "username": "oauth2"}))
    assert select_git_credential(session, "https://gitlab.example.com/team/repo.git", None) is None
    assert calls == []


def test_a_host_without_a_stored_credential_falls_back_to_the_configured_ones(capsys):
    session, _ = session_answering(Reply(ok=False))
    assert select_git_credential(session, "https://gitlab.example.com/team/repo.git", "task-1") is None
    out = capsys.readouterr().out
    assert "gitlab.example.com" in out and TOKEN not in out


def test_the_github_app_token_keeps_its_username():
    session, _ = session_answering(Reply(data={"token": TOKEN, "username": "x-access-token"}))
    assert fetch_git_credential(session, "task-1") == ("x-access-token", TOKEN)


def test_a_credential_without_a_username_uses_the_github_app_username():
    session, _ = session_answering(Reply(data={"token": TOKEN}))
    assert fetch_git_credential(session, "task-1") == ("x-access-token", TOKEN)


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="git is not installed")
def test_real_git_sends_the_stored_credential_to_a_non_github_host_and_not_in_the_url():
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
    url = "http://127.0.0.1:{}/team/repo.git".format(server.server_address[1])

    vcs = Git.__new__(Git)
    vcs.COMMAND_ENV = dict(Git.COMMAND_ENV)
    vcs._header_token = None
    vcs.use_header_token(TOKEN, username="oauth2")
    env = {**os.environ, **vcs.COMMAND_ENV, "GIT_CONFIG_GLOBAL": os.devnull}
    subprocess.run(["git", "ls-remote", url], env=env, capture_output=True)
    server.server_close()

    expected = "basic " + base64.b64encode("oauth2:{}".format(TOKEN).encode()).decode()
    assert seen and seen[0] == expected
    assert TOKEN not in url

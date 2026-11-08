import base64
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from cairnml_agent.helper.repo import (
    Git, basic_auth_header, fetch_git_credential, github_header, resolve_git_clone, scrub_token,
    select_git_credential,
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
    assert out == "The server has no Git credential for gitlab.example.com, using the configured credentials\n"


def test_a_public_repository_has_no_credential_and_no_message(capsys):
    session, _ = session_answering(Reply(data={"token": None, "expires_at": None, "username": None}))
    assert select_git_credential(session, "https://gitlab.example.com/team/repo.git", "task-1") is None
    assert fetch_git_credential(session, "task-1") is None
    assert capsys.readouterr().out == ""

    session, _ = session_answering(Reply(data={"token": TOKEN, "username": "oauth2"}))
    assert select_git_credential(session, "https://gitlab.example.com/team/repo.git", "task-1") == ("oauth2", TOKEN)


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


GITHUB_ADDRESSES = [
    "git@github.com:owner/name.git",
    "ssh://git@github.com/owner/name.git",
    "https://github.com/owner/name.git",
    "http://github.com/owner/name",
    "github.com/owner/name",
    "owner/name",
]

OTHER_HOST_ADDRESSES = [
    "git@gitlab.example.com:team/repo.git",
    "ssh://git@gitlab.example.com/team/repo.git",
    "http://gitlab.example.com/team/repo.git",
    "gitlab.example.com/team/repo.git",
]


@pytest.mark.parametrize("url", GITHUB_ADDRESSES)
def test_every_form_of_a_github_address_asks_the_server_for_a_token(url):
    session, calls = session_answering(Reply(data={"token": TOKEN, "username": "x-access-token",
                                                   "url": "https://github.com/owner/name.git"}))
    assert wants_stored_credential(url) is True
    assert select_git_credential(session, url, "task-1") == ("x-access-token", TOKEN)
    assert len(calls) == 1


@pytest.mark.parametrize("url", OTHER_HOST_ADDRESSES)
def test_other_hosts_are_not_asked_for_a_token(url):
    session, calls = session_answering(Reply(data={"token": TOKEN, "username": "oauth2",
                                                   "url": "https://gitlab.example.com/team/repo.git"}))
    assert wants_stored_credential(url) is False
    assert resolve_git_clone(session, url, "task-1") == (None, None)
    assert calls == []


def test_the_clone_address_is_the_one_the_server_gives():
    url = "https://github.com/owner/name.git"
    session, _ = session_answering(Reply(data={"token": TOKEN, "username": "x-access-token", "url": url}))
    assert resolve_git_clone(session, "git@github.com:owner/name.git", "task-1") == (url, ("x-access-token", TOKEN))


def test_a_public_answer_clones_the_server_address_without_a_credential(capsys):
    url = "https://github.com/owner/name.git"
    session, _ = session_answering(Reply(data={"token": None, "expires_at": None, "username": None, "url": url}))
    assert resolve_git_clone(session, "git@github.com:owner/name.git", "task-1") == (url, None)
    assert capsys.readouterr().out == ""


def test_an_answer_without_a_url_keeps_the_task_address_and_its_credential():
    session, _ = session_answering(Reply(data={"token": TOKEN, "username": None}))
    assert resolve_git_clone(session, "https://github.com/owner/name.git", "task-1") == (
        None, ("x-access-token", TOKEN))


def test_an_answer_without_a_url_gives_an_ssh_github_address_no_credential():
    session, calls = session_answering(Reply(data={"token": TOKEN, "username": None}))
    assert resolve_git_clone(session, "git@github.com:owner/name.git", "task-1") == (None, None)
    assert len(calls) == 1


def test_the_clone_uses_the_server_address_with_the_token_in_the_header_and_not_the_url(
        tmp_path, monkeypatch, capsys):
    url = "https://github.com/owner/name.git"
    session, _ = session_answering(Reply(data={"token": TOKEN, "username": "x-access-token", "url": url}))
    seen = _clone_with(monkeypatch, tmp_path, session, "git@github.com:owner/name.git")
    assert seen["url"] == url
    assert seen["header_token"] == TOKEN
    assert seen["env"]["GIT_CONFIG_VALUE_0"] == github_header(TOKEN)
    assert TOKEN not in seen["url"]
    assert TOKEN not in capsys.readouterr().out


def test_the_public_clone_uses_the_server_address_and_no_header(tmp_path, monkeypatch):
    url = "https://github.com/owner/name.git"
    session, _ = session_answering(Reply(data={"token": None, "username": None, "url": url}))
    seen = _clone_with(monkeypatch, tmp_path, session, "git@github.com:owner/name.git")
    assert seen["url"] == url
    assert seen["header_token"] is None
    assert "GIT_CONFIG_COUNT" not in seen["env"]


def test_an_older_server_without_a_url_gets_the_task_address_as_before(tmp_path, monkeypatch):
    session, _ = session_answering(Reply(data={"token": TOKEN, "username": None}))
    seen = _clone_with(monkeypatch, tmp_path, session, "https://github.com/owner/name.git")
    assert seen["url"] == "https://github.com/owner/name.git"
    assert seen["header_token"] == TOKEN


def _clone_with(monkeypatch, tmp_path, session, repository):
    """Run clone_repository_cached against a fake clone and return what git was asked to clone and with what."""
    import cairnml_agent.helper.repo as repo_module
    from cairnml_agent.helper.repo import RepoInfo, clone_repository_cached
    from cairnml_agent.external.pyhocon import ConfigTree

    seen = {}

    def fake_create(cls, session, execution_info, location):
        vcs = Git.__new__(Git)
        vcs.session = session
        vcs.log = None
        vcs.url = execution_info.repository
        vcs.location = str(location)
        vcs._header_token = None
        vcs._header_username = "x-access-token"
        vcs.COMMAND_ENV = dict(Git.COMMAND_ENV)
        return vcs

    def fake_clone(self, branch=None):
        seen["url"] = self.url
        seen["header_token"] = self._header_token
        seen["env"] = dict(self.COMMAND_ENV)
        Path = repo_module.Path
        Path(self.location).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(repo_module.VcsFactory, "create", classmethod(fake_create))
    monkeypatch.setattr(Git, "clone", fake_clone)
    monkeypatch.setattr(Git, "pull", lambda self: None)
    monkeypatch.setattr(Git, "checkout", lambda self: None)
    monkeypatch.setattr(Git, "get_repository_copy_info",
                        lambda self, path: RepoInfo(type="git", url="", branch="", commit="", root=""))
    monkeypatch.setattr(repo_module, "find_executable", lambda name: "/usr/bin/" + name)

    cache = tmp_path / "cache"
    cache.mkdir()
    session.config = ConfigTree(agent=ConfigTree(standalone_mode=False, translate_ssh=False, vcs_cache=ConfigTree(
        path=str(cache), enabled=False, clone_on_pull_fail=False)))
    session.get_logger = lambda name: __import__("logging").getLogger(name)
    session.debug_mode = False
    execution = SimpleNamespace(repository=repository)
    clone_repository_cached(session, execution, tmp_path / "dest", task_id="task-1")
    return seen

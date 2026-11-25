"""The agent reads a job's repository with the server's token or with none, never with the host's credentials (cairnml#194).

Real git against a local server that answers every request with 401 (a "private repository" for which the server gave
no token). The host is set up the way a developer's machine is: a credential helper in every place git reads one,
askpass programs, an SSH agent socket and a fake ``ssh`` first on the PATH. Each of them records its calls; none may be
called, and the server must never see an Authorization header the agent did not put there.
"""

import base64
import logging
import os
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest
from pyhocon import ConfigFactory, ConfigTree

from cairnml_agent.commands import worker as worker_module
from cairnml_agent.helper.repo import (
    Git,
    RepositoryAccessError,
    clone_repository_cached,
    resolve_git_clone,
)
from cairnml_agent.helper.repo import github_https_address

from .test_git_credentials import reply, session_answering

pytestmark = pytest.mark.skipif(not __import__("shutil").which("git"), reason="git is not installed")

TOKEN = "ghs_exampletoken123"


class _Server:
    def __init__(self):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append(self.headers.get("Authorization"))
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="private"')
                self.end_headers()

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path="o/n.git", userinfo=""):
        return "http://{}127.0.0.1:{}/{}".format(userinfo, self.port, path)

    def authorizations(self):
        return [value for value in self.requests if value]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    private = _Server()
    yield private
    private.close()


def _script(path, log):
    """An executable that records its arguments and, as a credential helper, offers a login."""
    path.write_text(
        '#!/bin/sh\necho "called: $0 $*" >> "{}"\necho username=host-user\necho password=host-secret\n'.format(log)
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class _Host:
    """What the machine offers a git command, and what was used of it."""

    def __init__(self, tmp_path, monkeypatch):
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.log = tmp_path / "calls.log"
        self.helper = _script(tmp_path / "helper.sh", self.log)
        (tmp_path / "bin").mkdir()
        self.ssh = _script(tmp_path / "bin" / "ssh", self.log)
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("PATH", "{}:{}".format(tmp_path / "bin", os.environ["PATH"]))
        monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "agent.sock"))
        monkeypatch.setenv("SSH_ASKPASS", self.helper)
        monkeypatch.setenv("GIT_ASKPASS", self.helper)
        monkeypatch.setenv("GIT_SSH", self.ssh)
        monkeypatch.setenv("GIT_SSH_COMMAND", self.ssh)
        for name in (
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_SYSTEM",
            "GIT_CONFIG_COUNT",
            "GIT_CONFIG_PARAMETERS",
            "XDG_CONFIG_HOME",
        ):
            monkeypatch.delenv(name, raising=False)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def register_helper(self, where):
        config = "[credential]\n\thelper = {}\n".format(self.helper)
        if where == "home":
            (self.home / ".gitconfig").write_text(config)
        elif where == "xdg":
            (self.home / ".config" / "git").mkdir(parents=True)
            (self.home / ".config" / "git" / "config").write_text(config)
        elif where == "global_env":
            (self.tmp_path / "global.cfg").write_text(config)
            self.monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(self.tmp_path / "global.cfg"))
        elif where == "system_env":
            (self.tmp_path / "system.cfg").write_text(config)
            self.monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(self.tmp_path / "system.cfg"))
        elif where == "count_env":
            self.monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
            self.monkeypatch.setenv("GIT_CONFIG_KEY_0", "credential.helper")
            self.monkeypatch.setenv("GIT_CONFIG_VALUE_0", self.helper)
        elif where == "parameters_env":
            self.monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'credential.helper={}'".format(self.helper))
        else:
            raise AssertionError(where)


@pytest.fixture
def host(tmp_path, monkeypatch):
    return _Host(tmp_path, monkeypatch)


def make_session(**agent_settings):
    agent = ConfigTree(
        standalone_mode=False, git_fetch_args=[], vcs_cache=ConfigTree(path="", enabled=False, clone_on_pull_fail=False)
    )
    for key, value in agent_settings.items():
        agent.put(key, value)
    return SimpleNamespace(
        config=ConfigTree(agent=agent), get_logger=lambda name: logging.getLogger(name), debug_mode=False
    )


def make_git(tmp_path, url, session=None):
    location = tmp_path / "work"
    location.mkdir(exist_ok=True)
    return Git(session or make_session(), url, str(location), "main")


HELPER_PLACES = ["home", "xdg", "global_env", "system_env", "count_env", "parameters_env"]


@pytest.mark.parametrize("where", HELPER_PLACES)
def test_a_credential_helper_of_the_host_is_never_called(host, server, tmp_path, where):
    host.register_helper(where)
    with pytest.raises(subprocess.CalledProcessError):
        make_git(tmp_path, server.url()).clone()
    assert host.calls() == []
    assert server.requests and server.authorizations() == []


def test_the_host_askpass_programs_and_ssh_are_never_called(host, server, tmp_path):
    with pytest.raises(subprocess.CalledProcessError):
        make_git(tmp_path, server.url()).clone()
    assert host.calls() == []
    assert server.authorizations() == []


@pytest.mark.parametrize("address", ["ssh://git@127.0.0.1:{port}/o/n.git", "git@127.0.0.1:o/n.git"])
def test_an_ssh_address_never_runs_ssh_even_when_it_reaches_git(host, server, tmp_path, address):
    with pytest.raises(subprocess.CalledProcessError):
        make_git(tmp_path, address.format(port=server.port)).clone()
    assert host.calls() == []


@pytest.mark.parametrize(
    "address",
    ["git@gitlab.example.com:o/n.git", "ssh://git@gitlab.example.com/o/n.git", "ssh://gitlab.example.com:2222/o/n"],
)
def test_an_ssh_address_of_another_host_than_github_is_refused_with_the_https_hint(host, address):
    session, calls = session_answering(reply(data={"token": TOKEN, "username": "x-access-token"}))
    with pytest.raises(RepositoryAccessError) as refused:
        resolve_git_clone(session, address, "task-1")
    assert "https" in str(refused.value) and address in str(refused.value)
    assert calls == [] and host.calls() == []


@pytest.mark.parametrize(
    "address",
    ["git@github.com:owner/name.git", "ssh://git@github.com/owner/name.git", "https://github.com/owner/name.git"],
)
def test_a_github_address_is_cloned_over_https_with_the_servers_token(address):
    session, _ = session_answering(reply(data={"token": TOKEN, "username": "x-access-token"}))
    clone_url, credential = resolve_git_clone(session, address, "task-1")
    assert credential == ("x-access-token", TOKEN)
    assert clone_url in (None, "https://github.com/owner/name.git")
    assert (clone_url is None) == address.startswith("https://")


def test_configured_git_credentials_are_not_used(host, server, tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRNML_AGENT_GIT_USER", "env-user")
    monkeypatch.setenv("CAIRNML_AGENT_GIT_PASS", "env-secret")
    monkeypatch.setenv("CAIRNML_AGENT_GIT_HOST", "127.0.0.1")
    session = make_session(
        git_user="cfg-user",
        git_pass="cfg-secret",
        git_host="127.0.0.1",
        git=ConfigFactory.from_dict(
            {"extra_credentials": [{"host": "127.0.0.1", "user": "extra-user", "pass": "extra-secret"}]}
        ),
    )
    git = make_git(tmp_path, server.url(), session)
    assert "cfg-secret" not in git.url_with_auth and "env-secret" not in git.url_with_auth
    with pytest.raises(subprocess.CalledProcessError):
        git.clone()
    assert server.authorizations() == []


def test_a_password_in_the_tasks_own_address_is_still_used(host, server, tmp_path):
    """The one credential a task carries itself (owner decision, cairnml#194): it is the task's, not the host's."""
    git = make_git(tmp_path, server.url(userinfo="bob:pw@"))
    with pytest.raises(subprocess.CalledProcessError):
        git.clone()
    expected = "Basic " + base64.b64encode(b"bob:pw").decode()
    assert expected in server.authorizations()


def test_the_servers_token_goes_only_to_the_host_of_the_clone_address(host, server, tmp_path):
    other = _Server()
    try:
        git = make_git(tmp_path, server.url())
        git.use_header_token(TOKEN, url=server.url())
        env = git._command_environment()
        for target in (server.url(), other.url()):
            subprocess.run(["git", "ls-remote", target], env=env, capture_output=True, cwd=str(tmp_path))
        assert server.authorizations()
        assert other.authorizations() == []
    finally:
        other.close()


def test_the_environment_of_a_git_command_holds_none_of_the_hosts_credential_settings(host, monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CONFIG_KEY_3", "credential.helper")
    monkeypatch.setenv("GIT_CONFIG_VALUE_3", "store")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "0")
    env = make_git(tmp_path, "https://github.com/o/n.git")._command_environment()
    for name in (
        "SSH_AUTH_SOCK",
        "SSH_ASKPASS",
        "GIT_ASKPASS",
        "GIT_SSH",
        "GIT_CONFIG_KEY_3",
        "GIT_CONFIG_VALUE_3",
        "GIT_CONFIG_COUNT",
    ):
        assert name not in env
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_SSH_COMMAND"] == "false"
    assert env["GIT_CONFIG_GLOBAL"] != str(host.home / ".gitconfig")
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_the_agent_writes_its_git_settings_to_a_file_of_its_own_not_to_the_hosts(host, tmp_path):
    (host.home / ".gitconfig").write_text("# the user's file\n")
    git = make_git(tmp_path, "https://github.com/o/n.git")
    assert (host.home / ".gitconfig").read_text() == "# the user's file\n"
    assert "directory = *" in open(git.COMMAND_ENV["GIT_CONFIG_GLOBAL"]).read()


def test_a_cached_clone_is_pulled_from_the_address_of_this_run_not_one_with_an_old_password(host, server, tmp_path):
    git = make_git(tmp_path, server.url())
    subprocess.run(["git", "init", "-q"], cwd=git.location, check=True, env=git._command_environment())
    subprocess.run(
        ["git", "remote", "add", "origin", server.url(userinfo="old:pw@")],
        cwd=git.location,
        check=True,
        env=git._command_environment(),
    )
    with pytest.raises(subprocess.CalledProcessError):
        git.pull()
    origin = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=git.location,
        capture_output=True,
        text=True,
        env=git._command_environment(),
    ).stdout.strip()
    assert "pw" not in origin and origin.startswith("http://127.0.0.1")


def _execution(url):
    return SimpleNamespace(repository=url, version_num=None, tag=None, branch="main")


def _clone_repository(session, server, tmp_path):
    session.config.put("agent.vcs_cache.path", str(tmp_path / "vcs"))
    (tmp_path / "vcs").mkdir(exist_ok=True)
    return clone_repository_cached(session, _execution(server.url()), tmp_path / "dest", task_id="task-1")


def _wire(token_answer):
    session, _ = session_answering(token_answer)
    wired = make_session()
    wired.send, wired.current = session.send, session.current
    return wired


def test_a_private_repository_without_a_token_fails_with_the_repository_and_both_remedies(host, server, tmp_path):
    session = _wire(reply(data={"token": None, "username": None}))
    with pytest.raises(RepositoryAccessError) as failure:
        _clone_repository(session, server, tmp_path)
    message = str(failure.value)
    assert server.url() in message
    assert "Repositories & Datasets" in message and "GitHub App" in message
    assert host.calls() == []


def test_a_refused_credential_request_fails_the_job_it_does_not_fall_back_to_anything(host, server, tmp_path):
    session = _wire(reply(ok=False))
    with pytest.raises(RepositoryAccessError) as failure:
        resolve_git_clone(session, "https://github.com/owner/name.git", "task-1")
    assert "refused or did not answer" in str(failure.value)
    assert "Repositories & Datasets" in str(failure.value)


def test_the_failure_message_is_the_status_message_of_the_task(monkeypatch, tmp_path):
    def refuse(**kwargs):
        raise RepositoryAccessError("The repository https://x/y.git could not be read: no token.")

    monkeypatch.setattr(worker_module, "clone_repository_cached", refuse)
    task = SimpleNamespace(id="t1", failed=lambda **kwargs: failures.append(kwargs))
    failures = []
    worker = SimpleNamespace(
        _session=SimpleNamespace(config=ConfigTree(), debug_mode=False),
        _standalone_mode=False,
        _task_status_change_message="changed",
    )
    with pytest.raises(worker_module.CommandFailedError):
        worker_module.Worker._get_repo_info(worker, _execution("https://x/y.git"), task, str(tmp_path))
    assert failures == [
        {
            "status_reason": "failed cloning repository",
            "status_message": "The repository https://x/y.git could not be read: no token.",
        }
    ]


def test_a_github_ssh_address_with_a_port_does_not_become_an_https_address_with_it():
    assert github_https_address("ssh://git@github.com:22/owner/name.git") == "https://github.com/owner/name.git"


def test_the_header_key_names_the_origin_of_the_address_not_its_user_or_password(host, tmp_path):
    git = make_git(tmp_path, "https://bob:pw@github.com/o/n.git")
    git.use_header_token(TOKEN, url="https://bob:pw@github.com/o/n.git")
    assert git.COMMAND_ENV["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"


def test_a_credential_request_that_raises_fails_the_job_with_the_clear_message():
    class Down:
        def current(self, request):
            return request

        def send(self, request):
            raise ConnectionError("the server cannot be reached")

    with pytest.raises(RepositoryAccessError) as failure:
        resolve_git_clone(Down(), "https://github.com/owner/name.git", "task-1")
    assert "refused or did not answer" in str(failure.value)


def test_the_refusal_of_an_ssh_address_does_not_print_a_password():
    session, _ = session_answering(reply(data={"token": TOKEN}))
    with pytest.raises(RepositoryAccessError) as failure:
        resolve_git_clone(session, "ssh://bob:hunter2@gitlab.example.com/o/n.git", "task-1")
    assert "hunter2" not in str(failure.value)

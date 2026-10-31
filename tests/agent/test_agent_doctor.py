import subprocess
from types import SimpleNamespace

import pytest
import requests

from cairnml_agent.commands import doctor

SERVER = "http://cairnml"


class FakeResponse:
    def __init__(self, status_code, data=None, message=""):
        self.status_code = status_code
        self.headers = {}
        self._body = {"data": data} if status_code == 200 else {"meta": {"result_msg": message}}

    def json(self):
        return self._body


class FakeServer:
    """Answers the sign-in settings call, as the server does without a token."""

    def __init__(self, reachable=True, github_app=True):
        self.reachable = reachable
        self.github_app = github_app

    def post(self, url, json=None, headers=None, timeout=None):
        if not self.reachable:
            raise requests.ConnectionError("refused")
        return FakeResponse(200, {"public_url": SERVER, "issuer": "x", "sdk_client_id": "cairnml-sdk",
                                  "web_url": "http://cairnml/", "github_app": self.github_app})


def write_conf(path, server=SERVER):
    path.write_text(f'api {{ api_server: "{server}"\n idp {{ device_id: "dev_agent" }} }}\n')
    return path


def docker_ok(args, **kwargs):
    return SimpleNamespace(returncode=0, stdout="Server Version: 27", stderr="")


def docker_down(args, **kwargs):
    return SimpleNamespace(returncode=1, stdout="", stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock.\n")


def by_name(checks):
    return {c.name: c for c in checks}


def run(conf, http=None, which=lambda name: "/usr/bin/docker", docker=docker_ok):
    return by_name(doctor.run_checks(conf, http=http or FakeServer(), which=which, run=docker))


def test_a_healthy_machine_passes_every_check(tmp_path):
    checks = doctor.run_checks(write_conf(tmp_path / "a.conf"), http=FakeServer(),
                               which=lambda name: "/usr/bin/docker", run=docker_ok)
    assert [c.name for c in checks] == ["config", "server", "docker", "daemon", "github"]
    assert all(c.ok is True and not c.warning for c in checks), [c.line() for c in checks]
    assert all(c.line().startswith("ok") for c in checks)


def test_a_missing_configuration_names_the_init_step(tmp_path):
    checks = run(tmp_path / "none.conf")
    assert checks["config"].ok is False
    assert "cairnml-agent init <server address>" in checks["config"].message
    assert checks["server"].ok is None


def test_an_unreachable_server_fails_the_server_and_skips_github(tmp_path):
    checks = run(write_conf(tmp_path / "a.conf"), http=FakeServer(reachable=False))
    assert checks["server"].ok is False
    assert "cannot reach" in checks["server"].message
    assert checks["github"].ok is None
    assert checks["docker"].ok is True  # the local checks do not depend on the server


def test_a_missing_docker_names_the_install_page(tmp_path):
    checks = run(write_conf(tmp_path / "a.conf"), which=lambda name: None)
    assert checks["docker"].ok is False
    assert "not installed" in checks["docker"].message
    assert doctor.DOCKER_DOCS in checks["docker"].message
    assert checks["daemon"].ok is None


def test_a_stopped_daemon_fails_with_the_reason_and_the_remedy(tmp_path):
    checks = run(write_conf(tmp_path / "a.conf"), docker=docker_down)
    assert checks["docker"].ok is True
    assert checks["daemon"].ok is False
    assert "Cannot connect to the Docker daemon" in checks["daemon"].message
    assert "sudo systemctl start docker" in checks["daemon"].message
    assert "docker group" in checks["daemon"].message


def test_a_daemon_that_does_not_answer_in_time_fails(tmp_path):
    def hangs(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 30)

    checks = run(write_conf(tmp_path / "a.conf"), docker=hangs)
    assert checks["daemon"].ok is False
    assert "did not answer" in checks["daemon"].message


def test_no_github_app_is_a_warning_with_the_remedy_and_passes(tmp_path):
    checks = run(write_conf(tmp_path / "a.conf"), http=FakeServer(github_app=False))
    assert checks["github"].ok is True and checks["github"].warning is True
    assert "jobs from github.com repositories are refused" in checks["github"].message
    assert "GitHub App" in checks["github"].message
    assert checks["github"].line().startswith("warn")


def test_a_configured_github_app_passes_without_warning(tmp_path):
    checks = run(write_conf(tmp_path / "a.conf"), http=FakeServer(github_app=True))
    assert checks["github"].ok is True and checks["github"].warning is False


def test_the_command_exits_zero_with_only_a_warning(tmp_path, capsys):
    conf = write_conf(tmp_path / "a.conf")
    code = doctor.doctor_main(file=str(conf), http=FakeServer(github_app=False),
                              which=lambda name: "/usr/bin/docker", run=docker_ok)
    assert code == 0
    assert "warn" in capsys.readouterr().out


def test_the_command_exits_one_when_docker_is_down(tmp_path, capsys):
    conf = write_conf(tmp_path / "a.conf")
    code = doctor.doctor_main(file=str(conf), http=FakeServer(), which=lambda name: "/usr/bin/docker",
                              run=docker_down)
    assert code == 1
    captured = capsys.readouterr()
    assert "FAIL daemon" in captured.out
    assert "did not pass" in captured.err


def test_doctor_is_a_subcommand_of_the_agent():
    from cairnml_agent.interface.worker import COMMANDS

    assert "doctor" in COMMANDS
    assert "--file" in COMMANDS["doctor"]["args"]

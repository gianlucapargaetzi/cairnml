"""cairnml-agent doctor: check this agent machine's setup and print one line per check.

    cairnml-agent doctor [--file FILE]

The checks run in this order; each failure names the next step:

    config   the configuration file names a server
    server   the server answers at that address
    docker   Docker is installed (the docker command is on the PATH)
    daemon   the Docker daemon runs and answers to this user
    github   the server has a GitHub App (a warning when not: jobs from github.com repositories are refused)

A warning prints as "warn" and does not change the exit status. The checks change nothing. The exit status is 0 when
no check fails, 1 otherwise.
"""
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import requests

from cairnml_agent.commands.config import default_config_file
from cairnml_agent.external.pyhocon import ConfigFactory
from cairnml_agent.idp import enrol as idp_enrol

DOCKER_DOCS = "https://docs.docker.com/engine/install/"
DOCKER_TIMEOUT_SEC = 30


@dataclass
class Check:
    name: str
    ok: Optional[bool]  # None when the check was skipped because an earlier check failed
    message: str
    warning: bool = False  # a passing check that needs the person's attention

    def line(self) -> str:
        status = "skip" if self.ok is None else ("FAIL" if not self.ok else ("warn" if self.warning else "ok"))
        return f"{status:<5}{self.name:<8}{self.message}"


def _fail(name: str, message: str, next_step: str) -> Check:
    return Check(name, False, f"{message} - next: {next_step}")


def read_server(conf: Path) -> Optional[str]:
    if not conf.is_file():
        return None
    try:
        return ConfigFactory.parse_file(str(conf)).get("api.api_server", None) or None
    except Exception:
        return None


def check_config(conf: Path, server: Optional[str]) -> Check:
    if not conf.is_file():
        return _fail("config", f"no configuration file at {conf}", "cairnml-agent init <server address>")
    if not server:
        return _fail("config", f"{conf} names no server", "cairnml-agent init <server address>")
    return Check("config", True, f"{conf} names {server}")


def check_server(server: Optional[str], http=None) -> Tuple[Check, dict]:
    """The server's sign-in settings, which also say whether a GitHub App is configured."""
    if not server:
        return Check("server", None, "skipped: the configuration names no server"), {}
    try:
        settings = idp_enrol._settings(http or requests.Session(), server)
    except idp_enrol.EnrolmentFailed as exc:
        return _fail("server", f"{server} refused the check ({exc})",
                     "give the server address that cairnml-agent init used, e.g. http://cairnml"), {}
    except requests.RequestException as exc:
        return _fail("server", f"cannot reach {server} ({exc.__class__.__name__})",
                     "check the address, that the server is running, and the port"), {}
    return Check("server", True, f"reachable at {server}"), settings


def check_docker(which=None) -> Check:
    path = (which or shutil.which)("docker")
    if not path:
        return _fail("docker", "the docker command is not installed or not on the PATH",
                     f"install Docker Engine ({DOCKER_DOCS}) and run this check again")
    return Check("docker", True, f"docker found at {path}")


def check_daemon(docker_check: Check, run=None) -> Check:
    """`docker info` answers only when the daemon runs and this user may talk to it."""
    if docker_check.ok is not True:
        return Check("daemon", None, "skipped: docker is not installed")
    try:
        result = (run or subprocess.run)(["docker", "info"], capture_output=True, text=True,
                                         timeout=DOCKER_TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _fail("daemon", f"docker info did not answer ({exc.__class__.__name__})", _daemon_step())
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        reason = detail[0] if detail else f"exit status {result.returncode}"
        return _fail("daemon", f"the Docker daemon does not answer to this user ({reason})", _daemon_step())
    return Check("daemon", True, "the Docker daemon runs and answers to this user")


def _daemon_step() -> str:
    return ("start the daemon with 'sudo systemctl start docker', add this user to the docker group with "
            "'sudo usermod -aG docker $USER' and log in again")


def check_github(settings: dict, server_check: Check) -> Check:
    """Jobs from github.com repositories need the server's GitHub App; without it they are refused at submission."""
    if server_check.ok is not True:
        return Check("github", None, "skipped: the server did not answer")
    if settings.get("github_app") is True:
        return Check("github", True, "the server has a GitHub App; it must be installed on each repository the jobs use")
    return Check("github", True, "no GitHub App on the server: jobs from github.com repositories are refused "
                                 "- next: set up the GitHub App (see the server README, section GitHub App)",
                 warning=True)


def run_checks(conf: Path, http=None, which=None, run=None) -> List[Check]:
    """Run every check and return them in the order they are printed."""
    server = read_server(conf)
    config = check_config(conf, server)
    server_check, settings = check_server(server, http=http)
    docker = check_docker(which=which)
    daemon = check_daemon(docker, run=run)
    return [config, server_check, docker, daemon, check_github(settings, server_check)]


def doctor_main(file=None, http=None, which=None, run=None) -> int:
    conf = Path(os.path.expanduser(file or default_config_file())).absolute()
    checks = run_checks(conf, http=http, which=which, run=run)
    for check in checks:
        print(check.line())
    failed = [check for check in checks if check.ok is not True]
    if failed:
        print(f"{len(failed)} of {len(checks)} checks did not pass", file=sys.stderr)
        return 1
    return 0

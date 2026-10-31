"""cairnml doctor: check this machine's setup and print one line per check.

    cairnml doctor [--file FILE]

The checks run in this order, each with the next step when it fails:

    config       the configuration file names a server
    server       the server answers at that address
    version      this SDK is at least the server's minimum version (warning when the server sends an upgrade notice)
    sign-in      this machine's device key signs in as a user on the server
    device key   the device key exists, belongs to this user and is readable only by its owner
    queues       every queue on the server has a worker listening to it (warning for each queue without one)
    storage      the object storage of the server accepts connections on port 9000 (artifacts, datasets)

A warning is printed as "warn" and does not change the exit status. The checks change nothing. The exit status is 0
when no check fails, 1 otherwise.
"""
import argparse
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import requests

from cairnml.cli.enrolment import ClientSession, default_config_file, read_enrolment
from cairnml.idp import enrol as idp_enrol
from cairnml.idp.device import DEVICE_KEY_PATH
from cairnml.idp.keys import read_private_key
from cairnml.version import __version__


STORAGE_PORT = 9000


def _open_port(host: str, port: int):
    return socket.create_connection((host, port), timeout=5)


@dataclass
class Check:
    name: str
    ok: Optional[bool]  # None when the check was skipped because an earlier check failed
    message: str
    warning: bool = False  # a passing check that needs the person's attention

    def line(self) -> str:
        status = "skip" if self.ok is None else ("FAIL" if not self.ok else ("warn" if self.warning else "ok"))
        return f"{status:<5}{self.name:<12}{self.message}"


def _fail(name: str, message: str, next_step: str) -> Check:
    return Check(name, False, f"{message} - next: {next_step}")


def check_config(conf: Path) -> Check:
    if not conf.is_file():
        return _fail("config", f"no configuration file at {conf}", "cairnml init <server address>")
    server, _, _ = read_enrolment(conf)
    if not server:
        return _fail("config", f"{conf} names no server", "cairnml init <server address>")
    return Check("config", True, f"{conf} names {server}")


def check_server(server: Optional[str], http=None) -> Check:
    if not server:
        return Check("server", None, "skipped: the configuration names no server")
    try:
        settings = idp_enrol._settings(http or requests.Session(), server)
    except idp_enrol.EnrolmentFailed as exc:
        return _fail("server", f"{server} refused the check ({exc})",
                     "give the server address that cairnml init used, e.g. http://cairnml")
    except requests.RequestException as exc:
        return _fail("server", f"cannot reach {server} ({exc.__class__.__name__})",
                     "check the address, that the server is running, and the port")
    return Check("server", True, f"reachable at {server} (public address {settings.get('public_url', 'unknown')})")


def check_device_key(key_path: Path) -> Check:
    if not key_path.exists():
        return _fail("device key", f"no device key at {key_path}", "cairnml init <server address>")
    try:
        read_private_key(key_path)
    except PermissionError as exc:
        return _fail("device key", str(exc), f"chmod 600 {key_path}")
    except Exception as exc:  # the file is not a valid Ed25519 key
        return _fail("device key", f"{key_path} cannot be read ({exc.__class__.__name__})",
                     "cairnml init <server address> --force")
    return Check("device key", True, f"{key_path} is readable by its owner only")


def check_sign_in(server: Optional[str], device: Optional[str], key_check: Check, key_path: Path,
                  http=None) -> Check:
    if not server:
        return Check("sign-in", None, "skipped: the configuration names no server")
    if not device:
        return _fail("sign-in", "this machine is not enrolled (signed out)", "cairnml init <server address>")
    if not key_check.ok:
        return Check("sign-in", None, "skipped: the device key is not usable")
    try:
        user = idp_enrol.whoami(server, device, key_path=key_path, http=http or requests.Session())
    except idp_enrol.EnrolmentFailed as exc:
        return _fail("sign-in", f"the server refused device {device} ({exc})",
                     "cairnml init <server address> --force, to enrol this machine again")
    except requests.RequestException as exc:
        return _fail("sign-in", f"cannot reach {server} ({exc.__class__.__name__})",
                     "fix the server check above first")
    return Check("sign-in", True, f"signed in as {user.get('name', 'unknown')} ({user.get('role', 'unknown')}) "
                                  f"with device {device}")


def check_storage(server: Optional[str]) -> Check:
    """Open a connection to the object storage, which the server runs on the host of its address, port 9000."""
    if not server:
        return Check("storage", None, "skipped: the configuration names no server")
    host = urlparse(server).hostname
    if not host:
        return Check("storage", None, "skipped: the server address names no host")
    try:
        _open_port(host, STORAGE_PORT).close()
    except OSError as exc:
        return _fail("storage", f"cannot open {host}:{STORAGE_PORT} to the object storage ({exc.__class__.__name__})",
                     f"open port {STORAGE_PORT} on {host} in the firewall, then run the check again")
    return Check("storage", True, f"object storage accepts connections at {host}:{STORAGE_PORT}")


def check_version(server_check: Check, client) -> Check:
    """The server answers an SDK older than its minimum with an upgrade notice; a current SDK gets none."""
    if server_check.ok is not True:
        return Check("version", None, "skipped: the server did not answer")
    notice = getattr(client, "upgrade", "")
    if notice:
        return Check("version", True, f"SDK {__version__} is below the server's minimum: {notice} "
                                      "- next: pip install -U cairnml", warning=True)
    return Check("version", True, f"SDK {__version__} is at or above the server's minimum")


def check_queues(server: Optional[str], device: Optional[str], sign_in: Check, key_path: Path, http=None) -> Check:
    """Every queue of the server should have a worker listening to it; a queue without one is a warning."""
    if not server:
        return Check("queues", None, "skipped: the configuration names no server")
    if sign_in.ok is not True or not device:
        return Check("queues", None, "skipped: sign-in did not pass")
    session = http or requests.Session()
    try:
        token = idp_enrol._token_for_device(session, server, device, key_path)
        base = idp_enrol._base(server)
        queues = idp_enrol._call(session, base + "queues.get_all", {}, token=token).get("queues") or []
        workers = idp_enrol._call(session, base + "workers.get_all", {}, token=token).get("workers") or []
    except idp_enrol.EnrolmentFailed as exc:
        return _fail("queues", f"the server refused the queue list ({exc})",
                     "check that this user may see the queues in the web app")
    except requests.RequestException as exc:
        return _fail("queues", f"cannot reach {server} ({exc.__class__.__name__})", "fix the server check first")
    names = [q.get("name", "") for q in queues]
    listening = {q.get("name", "") for worker in workers for q in (worker.get("queues") or [])}
    if not names:
        return Check("queues", True, "the server has no queue yet", warning=True)
    unserved = [name for name in names if name not in listening]
    if unserved:
        return Check("queues", True, f"no worker listens to {', '.join(unserved)} - next: run "
                                     f"cairnml-agent daemon --queue <name> on a machine with Docker",
                     warning=True)
    return Check("queues", True, f"{len(names)} queue(s), each with a listening worker")


def run_checks(conf: Path, key_path: Path = DEVICE_KEY_PATH, http=None) -> List[Check]:
    """Run every check and return them in the order they are printed."""
    server, device, _ = read_enrolment(conf)
    client = ClientSession(http)  # keeps the server's upgrade notice for the version check
    config = check_config(conf)
    server_check = check_server(server, http=client)
    version = check_version(server_check, client)
    key = check_device_key(Path(key_path))
    sign_in = check_sign_in(server, device, key, Path(key_path), http=client)
    queues = check_queues(server, device, sign_in, Path(key_path), http=client)
    return [config, server_check, version, sign_in, key, queues, check_storage(server)]


def doctor_main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cairnml doctor",
                                     description="Check this machine's setup: configuration, server, sign-in, "
                                                 "device key, object storage.")
    parser.add_argument("--file", "-F", default=default_config_file(),
                        help="configuration file (default: %(default)s)")
    args = parser.parse_args(argv)
    conf = Path(os.path.expanduser(args.file)).absolute()
    checks = run_checks(conf)
    for check in checks:
        print(check.line())
    failed = [check for check in checks if check.ok is not True]
    if failed:
        print(f"{len(failed)} of {len(checks)} checks did not pass", file=sys.stderr)
        return 1
    return 0

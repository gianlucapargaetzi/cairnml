"""This machine's enrolment with the CairnML server: cairnml-init, cairnml-whoami and cairnml-logout.

They replace the mlops enroll, whoami and logout commands. The server is named on the command line
(cairnml-init --server http://cairnml:8008); nothing is asked interactively.
"""
import argparse
import os
import socket
import sys
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests
from pathlib2 import Path

from cairnml.backend_config.defs import LOCAL_CONFIG_FILE_OVERRIDE_VAR, LOCAL_CONFIG_FILES
from cairnml.cli.config.__main__ import parse_known_host, write_config
from cairnml.idp.device import DEVICE_KEY_PATH
from cairnml.idp.enrol import EnrolmentFailed, enrol, revoke_device, whoami
from cairnml.utilities.pyhocon import ConfigFactory


def default_config_file() -> str:
    override = LOCAL_CONFIG_FILE_OVERRIDE_VAR.get()
    if override:
        return override
    for f in LOCAL_CONFIG_FILES:
        if os.path.exists(os.path.expanduser(os.path.expandvars(f))):
            return f
    return LOCAL_CONFIG_FILES[-1]


def read_enrolment(conf: Path) -> Tuple[Optional[str], Optional[str], dict]:
    """The server address and device id of this machine, and the rest of its addresses."""
    if not conf.is_file():
        return None, None, {}
    try:
        parsed = ConfigFactory.parse_file(str(conf))
    except Exception:
        return None, None, {}
    addresses = {
        "web": parsed.get("api.web_server", None),
    }
    return parsed.get("api.api_server", None), parsed.get("api.idp.device_id", None) or None, addresses


def _config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file", "-F", default=default_config_file(), help="configuration file (default: %(default)s)")


def init_main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cairnml-init", description="Enrol this machine with the CairnML server.")
    parser.add_argument("--server", required=True, help="the server's API address, e.g. http://cairnml:8008")
    parser.add_argument("--name", default=socket.gethostname(), help="the name of this machine (default: %(default)s)")
    parser.add_argument("--force", action="store_true",
                        help="enrol again: the previous device is revoked and the key replaced")
    _config_arg(parser)
    args = parser.parse_args(argv)

    conf = Path(os.path.expanduser(args.file)).absolute()
    _, previous, _ = read_enrolment(conf)
    api_server, _ = parse_known_host(urlparse(args.server))
    try:
        result = enrol(api_server or args.server, name=args.name, force=args.force, previous_device_id=previous)
    except EnrolmentFailed as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"error: cannot reach {api_server or args.server}: {exc.__class__.__name__}", file=sys.stderr)
        return 1

    write_config(conf, result.server_url, result.web_url, result.device_id)
    print(f"enrolled as {result.user or 'unknown user'} on {result.server_url} (device {result.device_id})")
    return 0


def whoami_main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cairnml-whoami", description="Show who this machine signs in as.")
    _config_arg(parser)
    args = parser.parse_args(argv)
    server, device, _ = read_enrolment(Path(os.path.expanduser(args.file)).absolute())
    if not device:
        print("not enrolled: run cairnml-init --server <server address>", file=sys.stderr)
        return 1
    try:
        user = whoami(server, device, key_path=DEVICE_KEY_PATH)
    except (EnrolmentFailed, requests.RequestException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{user.get('name', 'unknown')} ({user.get('role', 'unknown')}) on {server} as device {device}")
    return 0


def logout_main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cairnml-logout",
                                     description="Revoke this machine's enrolment and remove its key.")
    _config_arg(parser)
    args = parser.parse_args(argv)
    conf = Path(os.path.expanduser(args.file)).absolute()
    server, device, addresses = read_enrolment(conf)
    if not device:
        print("not enrolled: nothing to sign out")
        return 0
    try:
        revoke_device(server, device, key_path=DEVICE_KEY_PATH)
    except (EnrolmentFailed, requests.RequestException) as exc:
        # The key is kept: without it the device cannot be revoked later.
        print(f"error: the device was not revoked, so nothing was removed: {exc}", file=sys.stderr)
        return 1
    DEVICE_KEY_PATH.unlink(missing_ok=True)
    write_config(conf, server, addresses.get("web") or "", None)
    print(f"signed out: device {device} is revoked and its key removed from this machine")
    return 0

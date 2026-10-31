"""The devices enrolled under your sign-in: cairnml devices list and cairnml devices revoke.

    cairnml devices list
    cairnml devices revoke <id> [--yes]

Both sign in with this machine's device key, so the machine must be enrolled (cairnml init). An admin sees every
device; anyone else sees their own. Revoking asks for confirmation unless --yes is given.
"""
import argparse
import os
import sys
from pathlib2 import Path

import requests

from cairnml.cli.enrolment import ClientSession, _config_arg, read_enrolment
from cairnml.idp.device import DEVICE_KEY_PATH
from cairnml.idp.enrol import EnrolmentFailed, _base, _call, _token_for_device


def list_devices(server_url: str, device_id: str, key_path=None, http=None) -> list:
    """The devices of the user this device signs in as (all devices, for an admin), from the server."""
    http = http or ClientSession()
    key_path = Path(key_path or DEVICE_KEY_PATH)
    token = _token_for_device(http, server_url, device_id, key_path)
    return _call(http, _base(server_url) + "devices.get_all", {}, token=token)["devices"]


def revoke_other_device(server_url: str, device_id: str, target_id: str, key_path=None, http=None) -> dict:
    """Revoke another device of the same user, signing in with this device's key."""
    http = http or ClientSession()
    key_path = Path(key_path or DEVICE_KEY_PATH)
    token = _token_for_device(http, server_url, device_id, key_path)
    return _call(http, _base(server_url) + "devices.revoke", {"id": target_id}, token=token)


def _state(device: dict) -> str:
    return "revoked" if device.get("revoked") else "active"


def _print_table(devices: list) -> None:
    rows = [("ID", "NAME", "OWNER", "LAST SEEN", "STATE")]
    rows += [(d.get("id", ""), d.get("name") or "-", d.get("user", ""), d.get("last_used") or "never",
              _state(d)) for d in devices]
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())


def _enrolment(args) -> tuple:
    server, device, _ = read_enrolment(Path(os.path.expanduser(args.file)).absolute())
    if not device:
        print("not enrolled: run cairnml init <server address>", file=sys.stderr)
        return None, None
    return server, device


def list_main(argv) -> int:
    parser = argparse.ArgumentParser(prog="cairnml devices list",
                                     description="List the devices enrolled under your sign-in.")
    _config_arg(parser)
    args = parser.parse_args(argv)
    server, device = _enrolment(args)
    if not device:
        return 1
    try:
        devices = list_devices(server, device, key_path=DEVICE_KEY_PATH)
    except (EnrolmentFailed, requests.RequestException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not devices:
        print("no devices")
        return 0
    _print_table(devices)
    return 0


def revoke_main(argv) -> int:
    parser = argparse.ArgumentParser(prog="cairnml devices revoke",
                                     description="Revoke an enrolled device: its key stops working at once.")
    parser.add_argument("id", help="the device id, from cairnml devices list")
    parser.add_argument("--yes", "-y", action="store_true", help="revoke without asking for confirmation")
    _config_arg(parser)
    args = parser.parse_args(argv)
    server, device = _enrolment(args)
    if not device:
        return 1
    if not args.yes:
        try:
            answer = input(f"Revoke device {args.id}? It cannot be enrolled again without cairnml init. [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("not revoked")
            return 1
    try:
        revoked = revoke_other_device(server, device, args.id, key_path=DEVICE_KEY_PATH)
    except (EnrolmentFailed, requests.RequestException) as exc:
        print(f"error: the device was not revoked: {exc}", file=sys.stderr)
        return 1
    print(f"revoked device {revoked.get('id', args.id)} ({revoked.get('name') or 'unnamed'})")
    return 0


SUBCOMMANDS = {"list": list_main, "revoke": revoke_main}


def devices_main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print("usage: cairnml devices <list|revoke> [arguments]\n\n"
              "  list           list the devices enrolled under your sign-in\n"
              "  revoke <id>    revoke an enrolled device (asks first; --yes skips the question)\n\n"
              "Run 'cairnml devices <subcommand> --help' for the arguments.")
        return 0 if args else 2
    name = args[0]
    if name not in SUBCOMMANDS:
        print(f"cairnml devices: unknown subcommand '{name}' (use list or revoke)", file=sys.stderr)
        return 2
    return SUBCOMMANDS[name](args[1:])

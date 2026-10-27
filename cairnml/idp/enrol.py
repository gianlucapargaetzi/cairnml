"""Enrol, check and sign out this machine with the identity provider.

The same set as the mlops client (enroll, whoami and logout). The server's public address comes from
the server itself, and a device's assertion is addressed to it, so that address is what gets written.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import jwt
import requests

from .browser_login import browser_login
from .device import DEVICE_KEY_PATH, device_assertion
from .keys import generate_private_key, public_key_pem, write_private_key


class EnrolmentFailed(Exception):
    pass


@dataclass
class Enrolment:
    device_id: str
    server_url: str
    web_url: str
    user: str


def _call(http, url, body, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else None
    response = http.post(url, json=body, headers=headers, timeout=30)
    try:
        reply = response.json()
    except ValueError:
        reply = {}
    if response.status_code != 200:
        message = reply.get("meta", {}).get("result_msg")
        if not message:
            message = f"error {response.status_code}"
            if response.status_code in (404, 405):
                message += ": this is not the CairnML API address; give the address of the server, e.g. http://cairnml"
        raise EnrolmentFailed(message)
    return reply["data"]


def _base(server: str) -> str:
    return server.rstrip("/") + "/v2.8/"


def _settings(http, server: str) -> dict:
    return _call(http, _base(server) + "devices.idp_settings", {})


def _token_for_device(http, server_url: str, device_id: str, key_path: Path) -> str:
    """Prove the device's key to the server and get a short-lived token for it."""
    assertion = device_assertion(device_id, server_url, key_path=key_path)
    return _call(http, _base(server_url) + "devices.exchange", {"assertion": assertion})["access_token"]


def revoke_device(server_url: str, device_id: str, key_path=None, http=None) -> None:
    """Revoke a device with its own key: the owner may always revoke their own device."""
    http = http or requests.Session()
    key_path = Path(key_path or DEVICE_KEY_PATH)
    token = _token_for_device(http, server_url, device_id, key_path)
    _call(http, _base(server_url) + "devices.revoke", {"id": device_id}, token=token)


def whoami(server_url: str, device_id: str, key_path=None, http=None) -> dict:
    """The user this device signs in as, from the server (uses the device key, no browser)."""
    http = http or requests.Session()
    key_path = Path(key_path or DEVICE_KEY_PATH)
    token = _token_for_device(http, server_url, device_id, key_path)
    return _call(http, _base(server_url) + "users.get_current_user", {}, token=token)["user"]


def _replace_key(key, key_path: Path) -> None:
    """The key is written to a temporary file and moved into place, so a failure leaves no half key."""
    tmp = key_path.with_name(key_path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    write_private_key(tmp, key)
    os.replace(tmp, key_path)


def enrol(
    server: str,
    *,
    name: Optional[str] = None,
    kind: str = "client",
    force: bool = False,
    previous_device_id: Optional[str] = None,
    http=None,
    login=browser_login,
    key_path=None,
    out=print,
) -> Enrolment:
    """Sign in, and register this machine's new key. Refused when already enrolled, unless force is set.

    With force, the previous device is revoked first (with its own key, if the server still knows it),
    so the old person's key stops working.
    """
    key_path = Path(key_path or DEVICE_KEY_PATH)
    if (key_path.exists() or previous_device_id) and not force:
        raise EnrolmentFailed("this machine is already enrolled; use --force to enroll again")
    http = http or requests.Session()
    settings = _settings(http, server)
    server_url = settings["public_url"].rstrip("/")

    if force and previous_device_id and key_path.exists():
        try:
            revoke_device(server_url, previous_device_id, key_path=key_path, http=http)
            out(f"revoked the previous device {previous_device_id}")
        except EnrolmentFailed as exc:
            out(f"warning: the previous device could not be revoked ({exc}); revoke it in the web app")

    token = login(settings["issuer"], settings["sdk_client_id"], out=out)
    user = jwt.decode(token, options={"verify_signature": False}).get("preferred_username", "")

    key = generate_private_key()
    device = _call(
        http,
        _base(server_url) + "devices.enroll",
        {"kind": kind, "name": name or _hostname(), "public_key": public_key_pem(key)},
        token=token,
    )
    _replace_key(key, key_path)
    return Enrolment(
        device_id=device["id"],
        server_url=server_url,
        web_url=settings.get("web_url", ""),
        user=user,
    )


def enrol_device(api_host, *, kind="client", http=None, login=browser_login, key_path=None, name=None, out=print):
    """The agent's entry point: returns the device id. The server address is the server's public one."""
    return enrol(api_host, kind=kind, http=http, login=login, key_path=key_path, name=name, out=out).device_id


def _hostname() -> str:
    import socket

    return socket.gethostname()

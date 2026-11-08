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
from .device import DEVICE_KEY_PATH, device_assertion, secure_folder
from .keys import generate_private_key, public_key_pem, write_private_key


class EnrolmentFailed(Exception):
    """A refusal or a failure. status is the HTTP status of the server's answer, None when there was none."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


@dataclass
class Enrolment:
    device_id: str
    server_url: str
    web_url: str
    user: str
    notice: str = ""
    """ What the server asks the person who enrols to know; empty for a client """
    queue: str = ""
    """ The agent's own queue, named after its machine: the one queue this agent serves. Empty for a client """


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
        raise EnrolmentFailed(message, status=response.status_code)
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
    secure_folder(key_path.parent)
    tmp = key_path.with_name(key_path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    write_private_key(tmp, key)
    os.replace(tmp, key_path)


def _existing_enrolment(http, server_url: str, device_id, key_path: Path) -> Optional[bool]:
    """Whether this machine's enrolment still works on this server.

    True: the server accepts the device's key, so the machine is enrolled. False: the server refuses the device (another
    server, a revoked or deleted device, or no key), so the old enrolment is stale. None: the server could not say.
    """
    if not device_id or not key_path.exists():
        return False
    try:
        _token_for_device(http, server_url, device_id, key_path)
    except EnrolmentFailed as exc:
        return None if exc.status is None or exc.status >= 500 else False
    except (OSError, ValueError):
        return False  # the key cannot be read or used, so there are no valid credentials
    except requests.RequestException:
        return None
    return True


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
    http = http or requests.Session()
    settings = _settings(http, server)
    server_url = settings["public_url"].rstrip("/")
    if (key_path.exists() or previous_device_id) and not force:
        state = _existing_enrolment(http, server_url, previous_device_id, key_path)
        if state is True:
            raise EnrolmentFailed(f"this machine is already enrolled with {server_url}; use --force to enroll again")
        if state is None:
            raise EnrolmentFailed(f"cannot check the existing enrolment: {server_url} did not answer; try again, or use --force")
        # False: the server does not accept this device, so the enrolment is stale. Enrol again.

    if force and previous_device_id and key_path.exists():
        try:
            revoke_device(server_url, previous_device_id, key_path=key_path, http=http)
            out(f"revoked the previous device {previous_device_id}")
        except EnrolmentFailed as exc:
            out(f"warning: the previous device could not be revoked ({exc}); revoke it later with "
                f"'cairnml devices revoke {previous_device_id}' or in the web app under Settings > Devices")

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
        notice=device.get("notice", ""),
        queue=device.get("queue", ""),
    )


def enrol_device(api_host, *, kind="client", http=None, login=browser_login, key_path=None, name=None, out=print):
    """The agent's entry point: returns the device id. The server address is the server's public one."""
    result = enrol(api_host, kind=kind, http=http, login=login, key_path=key_path, name=name, out=out)
    if result.notice:
        out(f"note: {result.notice}")
    return result.device_id


def _hostname() -> str:
    import socket

    return socket.gethostname()

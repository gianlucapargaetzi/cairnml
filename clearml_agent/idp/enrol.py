"""Enrol this machine: sign in to the identity provider, register the device's public key."""

import socket
from pathlib import Path

import requests

from .browser_login import browser_login
from .device import DEVICE_KEY_PATH, save_device_key
from .keys import generate_private_key, public_key_pem


class EnrolmentFailed(Exception):
    pass


def _call(http, url, body, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else None
    response = http.post(url, json=body, headers=headers, timeout=30)
    try:
        reply = response.json()
    except ValueError:
        reply = {}
    if response.status_code != 200:
        message = reply.get("meta", {}).get("result_msg", f"error {response.status_code}")
        raise EnrolmentFailed(message)
    return reply["data"]


def enrol_device(
    api_host, *, kind="client", http=None, login=browser_login, key_path=None, name=None, out=print
):
    """Create this machine's key and register it. Returns the device id the server gave it.

    kind is "client" for a person's machine and "agent" for a machine that runs jobs; the server
    accepts an agent only from an admin.
    """
    key_path = Path(key_path or DEVICE_KEY_PATH)
    if key_path.exists():
        raise EnrolmentFailed(
            f"{key_path} already exists, so this machine is enrolled. Remove the file to enrol again."
        )
    http = http or requests.Session()
    base = api_host.rstrip("/") + "/v2.8/"

    settings = _call(http, base + "devices.idp_settings", {})
    token = login(settings["issuer"], settings["sdk_client_id"], out=out)

    key = generate_private_key()
    save_device_key(key_path, key)
    try:
        device = _call(
            http,
            base + "devices.enroll",
            {"kind": kind, "name": name or socket.gethostname(), "public_key": public_key_pem(key)},
            token=token,
        )
    except EnrolmentFailed:
        key_path.unlink()
        raise
    return device["id"]

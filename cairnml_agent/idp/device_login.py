"""Sign in with the device code flow: the machine shows a short code and an address, and the person signs in on any
other device. For machines without a browser, such as a headless server, a remote shell or a container.

The identity provider must allow the device grant for the client (oauth2.device.authorization.grant.enabled).
"""

import os
import sys
import time

import requests

from .browser_login import LoginFailed, pkce_pair

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def device_login(
    issuer,
    client_id,
    *,
    session=None,
    out=print,
    timeout=300.0,
    sleep=time.sleep,
    clock=time.monotonic,
):
    http = session or requests.Session()
    meta = http.get(issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=30).json()
    endpoint = meta.get("device_authorization_endpoint")
    if not endpoint:
        raise LoginFailed("the sign-in service does not offer the device sign-in for this client")

    # The client uses PKCE: the verifier is sent with each token request, the challenge with the device request.
    verifier, challenge = pkce_pair()
    response = http.post(
        endpoint,
        data={"client_id": client_id, "scope": "openid", "code_challenge": challenge, "code_challenge_method": "S256"},
        timeout=30,
    )
    body = response.json()
    if response.status_code != 200:
        raise LoginFailed(body.get("error_description") or body.get("error") or "the device sign-in did not start")

    address = body.get("verification_uri_complete") or body["verification_uri"]
    out(f"To sign in, open {address} on any device and enter the code {body['user_code']}.")
    out("Waiting for the sign-in to complete...")

    interval = float(body.get("interval", 5))
    deadline = clock() + min(timeout, float(body.get("expires_in", timeout)))
    while clock() < deadline:
        sleep(interval)
        response = http.post(
            meta["token_endpoint"],
            data={
                "grant_type": DEVICE_GRANT,
                "device_code": body["device_code"],
                "client_id": client_id,
                "code_verifier": verifier,
            },
            timeout=30,
        )
        reply = response.json()
        if response.status_code == 200:
            return reply["access_token"]
        error = reply.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise LoginFailed(reply.get("error_description") or error or "the device sign-in failed")
    raise LoginFailed("sign-in timed out")


def _browser_available() -> bool:
    """Linux without a graphical session cannot open the sign-in page for the person; other systems can."""
    if not sys.platform.startswith("linux"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def choose_login(device: bool):
    """The browser sign-in, or the device code when asked for or when no browser is available here."""
    from .browser_login import browser_login

    return device_login if device or not _browser_available() else browser_login

"""Sign in to the identity provider with the OAuth device flow (RFC 8628).

The tool prints a short code and a web address, opens the browser, and polls until the person
approves. Ported from the mlops client (packages/mlops-client/src/mlops/idp.py).
"""

import time
import webbrowser

import requests


class LoginFailed(Exception):
    pass


def device_login(
    issuer,
    client_id,
    *,
    session=None,
    sleep=time.sleep,
    out=print,
    open_browser=webbrowser.open,
):
    http = session or requests.Session()
    meta = http.get(issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=30).json()
    started = http.post(
        meta["device_authorization_endpoint"],
        data={"client_id": client_id, "scope": "openid"},
        timeout=30,
    )
    started.raise_for_status()
    device = started.json()
    url = device.get("verification_uri_complete") or device["verification_uri"]
    out(f"Open {url} and confirm the code {device['user_code']}")
    try:
        open_browser(url)
    except Exception:  # best effort: the address is already printed
        pass
    interval = device.get("interval", 5)
    deadline = time.monotonic() + device["expires_in"]
    while time.monotonic() < deadline:
        sleep(interval)
        response = http.post(
            meta["token_endpoint"],
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device["device_code"],
                "client_id": client_id,
            },
            timeout=30,
        )
        body = response.json()
        if response.status_code == 200:
            return body["access_token"]
        error = body.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise LoginFailed(body.get("error_description") or error or "login failed")
    raise LoginFailed("login timed out")

"""Sign in to the identity provider in the browser: authorization code with PKCE.

The browser opens the IdP's sign-in and grant screen. The IdP sends the person back to a listener
on 127.0.0.1, which takes the code and exchanges it for a token. Nothing is typed in the terminal.
"""

import base64
import hashlib
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import requests


class LoginFailed(Exception):
    pass


class LoginPortBusy(LoginFailed):
    """The fixed loopback port is in use, so the browser sign-in cannot receive its reply here."""


# The sign-in reply comes back to this loopback port. The identity provider registers this exact
# redirect address for cairnml-sdk and does not accept a port wildcard (Keycloak 26), so the default
# cannot be an ephemeral port until the realm registers more addresses. When another program holds
# the port, the device code sign-in is used instead.
LOGIN_PORT = 8765
# Overrides LOGIN_PORT. Use 0 to let the system choose a free port; the redirect address then carries
# the port that was actually bound. Only set it to a port the identity provider accepts for cairnml-sdk.
LOGIN_PORT_VAR = "CAIRNML_LOGIN_PORT"


def configured_login_port():
    raw = os.environ.get(LOGIN_PORT_VAR, "").strip()
    if not raw:
        return LOGIN_PORT
    try:
        port = int(raw)
    except ValueError:
        raise LoginFailed(f"{LOGIN_PORT_VAR} must be a port number, not {raw!r}") from None
    if not 0 <= port <= 65535:
        raise LoginFailed(f"{LOGIN_PORT_VAR} must be between 0 and 65535, not {port}")
    return port


def pkce_pair():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def browser_login(
    issuer,
    client_id,
    *,
    session=None,
    open_browser=webbrowser.open,
    out=print,
    timeout=300.0,
    port=None,
):
    if port is None:
        port = configured_login_port()
    http = session or requests.Session()
    meta = http.get(issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=30).json()
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    reply = {}

    class _Callback(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the name http.server requires
            parts = urlsplit(self.path)
            query = {key: values[0] for key, values in parse_qs(parts.query).items()}
            # Only a reply for this login counts. Another request on the port (a stray or forged callback)
            # is answered and ignored, so the wait goes on until the real reply or the timeout.
            if parts.path == "/callback" and query.get("state") == state:
                reply.update(query)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Signed in. You can close this tab.")
                return
            self.send_response(400 if parts.path == "/callback" else 404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"This reply does not match a sign-in in progress.")

        def log_message(self, *args):  # keep the terminal quiet
            pass

    try:
        listener = HTTPServer(("127.0.0.1", port), _Callback)
    except OSError as exc:
        raise LoginPortBusy(
            f"port {port} on 127.0.0.1 is in use by another program, and the sign-in cannot receive its reply "
            f"there ({exc.strerror or exc})"
        ) from None
    redirect = f"http://127.0.0.1:{listener.server_address[1]}/callback"
    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "scope": "openid",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Always ask for the credentials: the browser may still hold an earlier sign-in of another person.
        "prompt": "login",
    })
    url = f"{meta['authorization_endpoint']}?{query}"
    out(f"Signing in at {issuer}; your browser opens the sign-in page.")
    try:
        open_browser(url)
    except Exception:  # best effort: the address is printed below
        pass
    out(f"If the browser did not open, open this address: {url}")

    deadline = time.monotonic() + timeout
    listener.timeout = 1.0
    with listener:
        while "code" not in reply and "error" not in reply and time.monotonic() < deadline:
            listener.handle_request()
    if "error" in reply:
        raise LoginFailed(reply.get("error_description") or reply["error"])
    if "code" not in reply:
        raise LoginFailed("sign-in timed out")

    response = http.post(
        meta["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": reply["code"],
            "redirect_uri": redirect,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    body = response.json()
    if response.status_code != 200:
        raise LoginFailed(body.get("error_description") or body.get("error") or "token exchange failed")
    return body["access_token"]

"""Sign in to the identity provider in the browser: authorization code with PKCE.

The browser opens the IdP's sign-in and grant screen. The IdP sends the person back to a listener
on 127.0.0.1, which takes the code and exchanges it for a token. Nothing is typed in the terminal.
"""

import base64
import hashlib
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import requests


class LoginFailed(Exception):
    pass


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
):
    http = session or requests.Session()
    meta = http.get(issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=30).json()
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    reply = {}

    class _Callback(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the name http.server requires
            parts = urlsplit(self.path)
            if parts.path == "/callback":
                reply.update({key: values[0] for key, values in parse_qs(parts.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab.")

        def log_message(self, *args):  # keep the terminal quiet
            pass

    listener = HTTPServer(("127.0.0.1", 0), _Callback)
    redirect = f"http://127.0.0.1:{listener.server_address[1]}/callback"
    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "scope": "openid",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
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
    if reply.get("state") != state:
        raise LoginFailed("the sign-in reply did not match this request (state)")

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

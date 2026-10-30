import base64
import hashlib
import socket
import threading
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from cairnml_agent.idp.browser_login import LoginFailed, LoginPortBusy, browser_login

ISSUER = "https://idp.example/realms/mlops"


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeIdp:
    """Discovery and token endpoints. The browser is simulated by `open_browser` below."""

    def __init__(self, token_reply=(200, {"access_token": "idp-token"})):
        self.token_reply = token_reply
        self.token_posts = []

    def get(self, url, timeout=None):
        assert url == ISSUER + "/.well-known/openid-configuration"
        return Response(200, {
            "authorization_endpoint": ISSUER + "/auth",
            "token_endpoint": ISSUER + "/token",
        })

    def post(self, url, data=None, timeout=None):
        self.token_posts.append(dict(data))
        return Response(*self.token_reply)


def browser(answer):
    """Stands in for the person's browser: follows the redirect that the IdP would send."""
    seen = {}

    def open_browser(url):
        params = parse_qs(urlsplit(url).query)
        seen.update({k: v[0] for k, v in params.items()})
        redirect = params["redirect_uri"][0]
        query = answer(params)
        threading.Thread(target=lambda: requests.get(redirect + "?" + query, timeout=10)).start()
        return True

    return open_browser, seen


def approve(params):
    return f"code=the-code&state={params['state'][0]}"


def test_the_person_signs_in_and_the_token_is_returned():
    idp = FakeIdp()
    open_browser, seen = browser(approve)
    token = browser_login(ISSUER, "cairnml-sdk", session=idp, open_browser=open_browser, timeout=20)
    assert token == "idp-token"

    challenge = seen["code_challenge"]
    verifier = idp.token_posts[0]["code_verifier"]
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert seen["response_type"] == "code"
    assert seen["code_challenge_method"] == "S256"
    assert seen["client_id"] == "cairnml-sdk"
    assert idp.token_posts[0]["code"] == "the-code"
    assert idp.token_posts[0]["redirect_uri"] == seen["redirect_uri"]
    assert idp.token_posts[0]["grant_type"] == "authorization_code"


def test_the_redirect_goes_to_the_loopback_address_only():
    open_browser, seen = browser(approve)
    browser_login(ISSUER, "cairnml-sdk", session=FakeIdp(), open_browser=open_browser, timeout=20)
    assert seen["redirect_uri"].startswith("http://127.0.0.1:")


def test_a_stray_callback_with_another_state_does_not_end_the_sign_in():
    idp = FakeIdp()
    seen = {}

    def open_browser(url):
        params = parse_qs(urlsplit(url).query)
        seen.update({k: v[0] for k, v in params.items()})
        redirect = params["redirect_uri"][0]

        def reply():
            # A local request with the wrong state (or an error without it) comes first, then the real reply.
            stray = requests.get(redirect + "?error=x", timeout=10)
            forged = requests.get(redirect + "?code=forged&state=forged", timeout=10)
            assert stray.status_code == 400 and forged.status_code == 400
            requests.get(redirect + "?code=the-code&state=" + params["state"][0], timeout=10)

        threading.Thread(target=reply).start()
        return True

    token = browser_login(ISSUER, "cairnml-sdk", session=idp, open_browser=open_browser, timeout=20)
    assert token == "idp-token"
    assert idp.token_posts[0]["code"] == "the-code"


def test_a_stray_callback_alone_ends_in_a_timeout_not_an_error():
    open_browser, _ = browser(lambda params: "error=x&state=forged")
    with pytest.raises(LoginFailed, match="timed out"):
        browser_login(ISSUER, "cairnml-sdk", session=FakeIdp(), open_browser=open_browser, timeout=2)


def test_a_busy_port_is_reported_before_the_browser_opens():
    busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    opened = []
    try:
        port = busy.getsockname()[1]
        with pytest.raises(LoginPortBusy, match="in use"):
            browser_login(ISSUER, "cairnml-sdk", session=FakeIdp(), open_browser=lambda url: opened.append(url),
                          timeout=5, port=port)
    finally:
        busy.close()
    assert opened == []


def test_a_refused_sign_in_is_reported():
    open_browser, _ = browser(
        lambda params: f"error=access_denied&error_description=not+allowed&state={params['state'][0]}"
    )
    with pytest.raises(LoginFailed, match="not allowed"):
        browser_login(ISSUER, "cairnml-sdk", session=FakeIdp(), open_browser=open_browser, timeout=20)


def test_a_failed_token_exchange_is_reported():
    idp = FakeIdp(token_reply=(400, {"error": "invalid_grant", "error_description": "code used"}))
    open_browser, _ = browser(approve)
    with pytest.raises(LoginFailed, match="code used"):
        browser_login(ISSUER, "cairnml-sdk", session=idp, open_browser=open_browser, timeout=20)


def test_no_sign_in_in_time_is_a_timeout():
    with pytest.raises(LoginFailed, match="timed out"):
        browser_login(ISSUER, "cairnml-sdk", session=FakeIdp(), open_browser=lambda url: True, timeout=1)


def test_the_reply_comes_back_to_the_registered_port():
    from cairnml_agent.idp.browser_login import LOGIN_PORT

    open_browser, seen = browser(approve)
    browser_login(ISSUER, "cairnml-sdk", session=FakeIdp(), open_browser=open_browser, timeout=20)
    assert seen["redirect_uri"] == f"http://127.0.0.1:{LOGIN_PORT}/callback"
    assert LOGIN_PORT == 8765


def test_the_sign_in_always_asks_for_the_credentials():
    open_browser, seen = browser(approve)
    browser_login(ISSUER, "cairnml-sdk", session=FakeIdp(), open_browser=open_browser, timeout=20)
    assert seen["prompt"] == "login"

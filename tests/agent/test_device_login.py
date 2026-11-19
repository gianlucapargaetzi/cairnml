import base64
import hashlib

import pytest

from cairnml.idp.browser_login import LoginFailed, LoginPortBusy
from cairnml.idp.device_login import browser_or_device_login, device_login


class Reply:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class FakeSession:
    """Serves the discovery document, the device authorization, and a scripted sequence of token replies."""

    def __init__(self, token_replies, device=None, discovery=None):
        self.token_replies = list(token_replies)
        self.device = (
            device
            if device is not None
            else {
                "device_code": "dc",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://idp/device",
                "verification_uri_complete": "https://idp/device?user_code=ABCD-EFGH",
                "interval": 5,
                "expires_in": 600,
            }
        )
        self.discovery = (
            discovery
            if discovery is not None
            else {
                "device_authorization_endpoint": "https://idp/device-auth",
                "token_endpoint": "https://idp/token",
            }
        )
        self.calls = []

    def get(self, url, timeout):
        return Reply(200, self.discovery)

    def post(self, url, data, timeout):
        self.calls.append((url, data))
        if url.endswith("device-auth"):
            return Reply(200, self.device)
        return self.token_replies.pop(0)


def run(session, sleeps=None):
    lines = []
    token = device_login(
        "https://idp",
        "cairnml-sdk",
        session=session,
        out=lines.append,
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
        clock=iter([0.0, 1.0, 2.0, 3.0]).__next__,
    )
    return token, lines


def test_the_code_and_address_are_shown_and_the_token_is_returned_after_pending_replies():
    session = FakeSession([Reply(400, {"error": "authorization_pending"}), Reply(200, {"access_token": "tok"})])
    token, lines = run(session)
    assert token == "tok"
    assert any("ABCD-EFGH" in line and "device?user_code=ABCD-EFGH" in line for line in lines)
    assert [data["grant_type"] for _, data in session.calls if "device_code" in data] == [
        "urn:ietf:params:oauth:grant-type:device_code"
    ] * 2


def test_slow_down_lengthens_the_wait():
    session = FakeSession([Reply(400, {"error": "slow_down"}), Reply(200, {"access_token": "tok"})])
    sleeps = []
    run(session, sleeps)
    assert sleeps == [5.0, 10.0]


def test_a_refused_sign_in_is_reported():
    session = FakeSession([Reply(400, {"error": "access_denied", "error_description": "denied by the person"})])
    with pytest.raises(LoginFailed, match="denied by the person"):
        run(session)


def test_a_provider_without_the_device_grant_is_reported_clearly():
    session = FakeSession([], discovery={"token_endpoint": "https://idp/token"})
    with pytest.raises(LoginFailed, match="does not offer the device sign-in"):
        run(session)


def test_discovery_and_device_request_carry_the_pkce_challenge_and_the_client():
    session = FakeSession([Reply(200, {"access_token": "tok"})])
    run(session)
    (device_url, device_data), (token_url, token_data) = session.calls
    assert device_url == "https://idp/device-auth"
    assert device_data["client_id"] == "cairnml-sdk" and device_data["scope"] == "openid"
    assert device_data["code_challenge_method"] == "S256"
    # the challenge is the S256 of the verifier that the token request sends
    digest = hashlib.sha256(token_data["code_verifier"].encode()).digest()
    assert device_data["code_challenge"] == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert token_url == "https://idp/token"
    assert token_data["device_code"] == "dc" and token_data["client_id"] == "cairnml-sdk"


def test_the_plain_address_is_shown_when_no_complete_address_is_given():
    device = {"device_code": "dc", "user_code": "WXYZ", "verification_uri": "https://idp/device", "interval": 1}
    _, lines = run(FakeSession([Reply(200, {"access_token": "tok"})], device=device))
    assert lines[0] == "To sign in, open https://idp/device on any device and enter the code WXYZ."
    assert lines[1] == "Waiting for the sign-in to complete..."


def test_a_device_request_that_is_refused_is_reported_with_the_reason():
    class Refusing(FakeSession):
        def post(self, url, data, timeout):
            return Reply(400, {"error": "invalid_client", "error_description": "unknown client"})

    with pytest.raises(LoginFailed, match="unknown client"):
        run(Refusing([]))


def test_the_sign_in_gives_up_when_the_code_expires():
    session = FakeSession([Reply(400, {"error": "authorization_pending"})] * 5)
    with pytest.raises(LoginFailed, match="sign-in timed out"):
        # the clock passes the deadline (expires_in, capped by the time-out) after the first poll
        device_login(
            "https://idp",
            "cairnml-sdk",
            session=session,
            out=lambda m: None,
            timeout=2.0,
            sleep=lambda s: None,
            clock=iter([0.0, 1.0, 3.0]).__next__,
        )
    assert len([1 for _, data in session.calls if "device_code" in data]) == 1


def test_an_unknown_token_error_is_reported_by_its_code():
    with pytest.raises(LoginFailed, match="expired_token"):
        run(FakeSession([Reply(400, {"error": "expired_token"})]))


def test_the_cli_uses_the_device_code_when_asked_or_when_no_browser_is_there(monkeypatch):
    import cairnml.cli.enrolment as mod

    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert mod.choose_login(device=False) is device_login
    assert mod.choose_login(device=True) is device_login

    monkeypatch.setenv("DISPLAY", ":0")
    assert mod.choose_login(device=False) is browser_or_device_login
    assert mod.choose_login(device=True) is device_login

    monkeypatch.delenv("DISPLAY")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert mod.choose_login(device=False) is browser_or_device_login

    # other systems than Linux can always open a browser
    monkeypatch.delenv("WAYLAND_DISPLAY")
    monkeypatch.setattr(mod.sys, "platform", "darwin")
    assert mod.choose_login(device=False) is browser_or_device_login
    assert mod.choose_login(device=True) is device_login


def test_a_busy_browser_port_falls_back_to_the_device_code(monkeypatch):
    import cairnml.idp.device_login as module

    def busy_browser(issuer, client_id, **kwargs):
        raise LoginPortBusy("port 8765 on 127.0.0.1 is in use")

    messages = []
    monkeypatch.setattr(module, "browser_login", busy_browser)
    monkeypatch.setattr(module, "device_login", lambda issuer, client_id, out=print: "device-token")
    token = module.browser_or_device_login("https://idp.example/realms/mlops", "cairnml-sdk", out=messages.append)
    assert token == "device-token"
    assert "port 8765" in messages[0] and "device code" in messages[0]


def test_other_browser_failures_are_not_replaced_by_the_device_code(monkeypatch):
    import cairnml.idp.device_login as module

    def refused(issuer, client_id, **kwargs):
        raise LoginFailed("not allowed")

    monkeypatch.setattr(module, "browser_login", refused)
    monkeypatch.setattr(module, "device_login", lambda *a, **k: pytest.fail("device sign-in must not start"))
    with pytest.raises(LoginFailed, match="not allowed"):
        module.browser_or_device_login("https://idp.example/realms/mlops", "cairnml-sdk", out=lambda m: None)

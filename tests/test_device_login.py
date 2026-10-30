import pytest

from cairnml.idp.browser_login import LoginFailed
from cairnml.idp.device_login import device_login


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
        self.device = device if device is not None else {
            "device_code": "dc", "user_code": "ABCD-EFGH", "verification_uri": "https://idp/device",
            "verification_uri_complete": "https://idp/device?user_code=ABCD-EFGH", "interval": 5, "expires_in": 600,
        }
        self.discovery = discovery if discovery is not None else {
            "device_authorization_endpoint": "https://idp/device-auth", "token_endpoint": "https://idp/token",
        }
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
        "https://idp", "cairnml-sdk", session=session, out=lines.append,
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)), clock=iter([0.0, 1.0, 2.0, 3.0]).__next__,
    )
    return token, lines


def test_the_code_and_address_are_shown_and_the_token_is_returned_after_pending_replies():
    session = FakeSession([Reply(400, {"error": "authorization_pending"}), Reply(200, {"access_token": "tok"})])
    token, lines = run(session)
    assert token == "tok"
    assert any("ABCD-EFGH" in line and "device?user_code=ABCD-EFGH" in line for line in lines)
    assert [data["grant_type"] for _, data in session.calls if "device_code" in data] == [
        "urn:ietf:params:oauth:grant-type:device_code"] * 2


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


def test_the_cli_uses_the_device_code_when_asked_or_when_no_browser_is_there(monkeypatch):
    from cairnml.cli import enrolment
    from cairnml.idp.browser_login import browser_login

    monkeypatch.setattr(enrolment.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert enrolment.choose_login(device=False) is device_login
    assert enrolment.choose_login(device=True) is device_login

    monkeypatch.setenv("DISPLAY", ":0")
    assert enrolment.choose_login(device=False) is browser_login
    assert enrolment.choose_login(device=True) is device_login

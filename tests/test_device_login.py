import pytest

from clearml.idp.device_login import LoginFailed, device_login

ISSUER = "https://idp.example/realms/mlops"


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeHttp:
    """Answers the three IdP calls in order. `polls` is the list of token responses."""

    def __init__(self, polls, expires_in=60):
        self.polls = list(polls)
        self.expires_in = expires_in
        self.posts = []

    def get(self, url, timeout=None):
        assert url == ISSUER + "/.well-known/openid-configuration"
        return Response(200, {
            "device_authorization_endpoint": ISSUER + "/device",
            "token_endpoint": ISSUER + "/token",
        })

    def post(self, url, data=None, timeout=None):
        self.posts.append((url, dict(data)))
        if url.endswith("/device"):
            return Response(200, {
                "device_code": "dev-code",
                "user_code": "WDJB-MJHT",
                "verification_uri": ISSUER + "/device",
                "verification_uri_complete": ISSUER + "/device?user_code=WDJB-MJHT",
                "expires_in": self.expires_in,
                "interval": 5,
            })
        status, body = self.polls.pop(0)
        return Response(status, body)


def run(http):
    shown, opened, sleeps = [], [], []
    token = device_login(
        ISSUER, "clearml-sdk", session=http, sleep=sleeps.append,
        out=shown.append, open_browser=opened.append,
    )
    return token, shown, opened, sleeps


def test_returns_the_token_once_the_person_approves():
    http = FakeHttp([(400, {"error": "authorization_pending"}), (200, {"access_token": "t-1"})])
    token, shown, opened, sleeps = run(http)
    assert token == "t-1"
    assert "WDJB-MJHT" in shown[0]
    assert opened == [ISSUER + "/device?user_code=WDJB-MJHT"]
    assert sleeps == [5, 5]


def test_slow_down_waits_longer_before_the_next_poll():
    http = FakeHttp([(400, {"error": "slow_down"}), (200, {"access_token": "t"})])
    _, _, _, sleeps = run(http)
    assert sleeps == [5, 10]


def test_a_refused_login_is_reported():
    http = FakeHttp([(400, {"error": "access_denied", "error_description": "not allowed"})])
    with pytest.raises(LoginFailed, match="not allowed"):
        run(http)


def test_an_expired_code_is_a_timeout():
    http = FakeHttp([], expires_in=0)
    with pytest.raises(LoginFailed, match="timed out"):
        run(http)


def test_the_device_code_is_sent_with_the_device_grant():
    http = FakeHttp([(200, {"access_token": "t"})])
    run(http)
    token_post = [data for url, data in http.posts if url.endswith("/token")][0]
    assert token_post["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
    assert token_post["device_code"] == "dev-code"
    assert token_post["client_id"] == "clearml-sdk"

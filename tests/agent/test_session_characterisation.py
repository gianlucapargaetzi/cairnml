"""Characterisation of the agent's session: what the agent relies on, pinned before the session is taken from the SDK.

The tests build a real session with the HTTP layer faked at requests.Session.request, so they say what goes on the wire
and what the session decides, not how the code is laid out.
"""

from unittest import mock

import jwt
import pytest
import requests
from cairnml.backend_api.session.session import Session as SdkSession
from cairnml.idp.device import save_device_key, secure_folder
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cairnml_agent.backend_api.config import load
from cairnml_agent.backend_api.session.session import LoginError, Session
from cairnml_agent.idp import device as agent_device
from cairnml_agent.version import __version__

HOST = "https://api.example/cairnml/api"
TOKEN_EXP = 4102444800
CLASS_ATTRIBUTES = (
    "api_version",
    "max_api_version",
    "server_version",
    "feature_set",
    "user_id",
    "force_max_api_version",
)


class Reply:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"meta": {"result_code": 200}, "data": {}}
        self.reason = "reason"
        self.text = str(self._body)

    def json(self):
        return self._body


def token(**claims):
    claims.setdefault("exp", TOKEN_EXP)
    return jwt.encode(claims, "x" * 32, algorithm="HS256")


@pytest.fixture(autouse=True)
def isolated_session_state(monkeypatch, tmp_path):
    """The session keeps server facts in class attributes; each test gets them back as they were. No local
    configuration file is read, so no configuration or device key of the person running the tests is used."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # the local files were named from the home folder when the agent was imported: point the config at no file
    monkeypatch.setenv("CAIRNML_CONFIG_FILE", str(tmp_path / "no-such.conf"))
    # the server facts are set on the class that reads the token: the agent's, or the SDK's session it is built on
    saved = {cls: {n: vars(cls)[n] for n in CLASS_ATTRIBUTES if n in vars(cls)} for cls in (Session, SdkSession)}
    for var in (
        "CAIRNML_API_HOST",
        "CAIRNML_AUTH_TOKEN",
        "CAIRNML_JOB_TOKEN",
        "CAIRNML_JOB_TOKEN_FILE",
        "CAIRNML_AGENT_INITIAL_CONNECT_RETRY_OVERRIDE",
        "CAIRNML_NO_DEFAULT_SERVER",
        "CAIRNML_API_HOST_VERIFY_CERT",
        "CAIRNML_API_DEFAULT_REQ_METHOD",
        "CAIRNML_DISABLE_VAULT_SUPPORT",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
    for cls, values in saved.items():
        for name in CLASS_ATTRIBUTES:
            if name in values:
                setattr(cls, name, values[name])
            elif name in vars(cls):
                delattr(cls, name)


@pytest.fixture
def wire(monkeypatch):
    """Every HTTP request the session makes, and the reply to it. Set wire.reply to change the answer."""

    class Wire:
        def __init__(self):
            self.calls = []
            self.reply = Reply()
            self.errors = []

    state = Wire()

    def fake_request(self, method, url, **kwargs):
        state.calls.append({"method": method, "url": url, **kwargs})
        if state.errors:
            raise state.errors.pop(0)
        return state.reply

    monkeypatch.setattr(requests.Session, "request", fake_request)
    return state


def make_session(monkeypatch, tmp_path, *, auth_token=None, config_values=None, **kwargs):
    if auth_token is None:
        auth_token = token(api_version="2.9", feature_set="basic", server_version="2.9.0", identity={"user": "u1"})
    monkeypatch.setenv("CAIRNML_AUTH_TOKEN", auth_token)
    config = load()
    for key, value in (config_values or {}).items():
        config.put(key, value)
    kwargs.setdefault("config", config)
    kwargs.setdefault("host", None)
    return Session(**kwargs)


def sign_in_recorder(monkeypatch):
    """Records the connect retries of the HTTP session in use at each sign-in, and still signs in."""
    seen = []
    real = Session.refresh_token

    def recording_refresh(self):
        adapter = self._Session__http_session.get_adapter(self.host)
        seen.append(adapter.max_retries.connect)
        return real(self)

    monkeypatch.setattr(Session, "refresh_token", recording_refresh)
    return seen


# ---- host and token come from the agent's configuration -----------------------------------------------------------


def test_the_host_argument_wins_and_the_trailing_slash_is_dropped(monkeypatch, tmp_path, wire):
    session = make_session(monkeypatch, tmp_path, host="https://arg.example/cairnml/api/")
    assert session.host == "https://arg.example/cairnml/api"


def test_the_environment_host_wins_over_the_configured_host(monkeypatch, tmp_path, wire):
    monkeypatch.setenv("CAIRNML_API_HOST", "https://env.example/cairnml/api/")
    session = make_session(monkeypatch, tmp_path, config_values={"api.api_server": "https://cfg.example/cairnml/api"})
    assert session.host == "https://env.example/cairnml/api"


def test_the_configured_api_server_wins_over_the_api_host_key(monkeypatch, tmp_path, wire):
    session = make_session(
        monkeypatch,
        tmp_path,
        config_values={"api.api_server": "https://server.example/cairnml/api", "api.host": "https://host.example"},
    )
    assert session.host == "https://server.example/cairnml/api"


def test_the_configured_api_host_is_used_when_no_api_server_is_set(monkeypatch, tmp_path, wire):
    session = make_session(monkeypatch, tmp_path, config_values={"api.host": "https://host.example/cairnml/api"})
    assert session.host == "https://host.example/cairnml/api"


def test_without_any_host_the_agent_refuses_to_guess_one(monkeypatch, tmp_path, wire):
    with pytest.raises(ValueError, match="Could not find host server definition"):
        make_session(monkeypatch, tmp_path)


def test_the_environment_token_is_the_token_the_session_uses(monkeypatch, tmp_path, wire):
    given = token(api_version="2.9", feature_set="basic", identity={"user": "u9"})
    session = make_session(monkeypatch, tmp_path, auth_token=given, host=HOST)
    assert session.token == given


def test_the_configured_device_id_is_read(monkeypatch, tmp_path, wire):
    monkeypatch.delenv("CAIRNML_AUTH_TOKEN", raising=False)
    config = load()
    config.put("api.idp.device_id", "dev_cfg")
    key = tmp_path / "device.key"
    save_device_key(key, Ed25519PrivateKey.generate())
    with mock.patch.object(agent_device, "DEVICE_KEY_PATH", key):
        wire.reply = Reply(200, {"data": {"access_token": token(api_version="2.9")}})
        session = Session(config=config, host=HOST)
    assert session.device_id == "dev_cfg"


def test_nothing_to_sign_in_with_says_the_machine_is_not_enrolled(monkeypatch, tmp_path, wire):
    monkeypatch.delenv("CAIRNML_AUTH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="not enrolled"):
        Session(config=load(), host=HOST)


# ---- headers ----------------------------------------------------------------------------------------------------


def test_requests_carry_the_bearer_token_and_the_agent_identity_headers(monkeypatch, tmp_path, wire):
    session = make_session(monkeypatch, tmp_path, host=HOST, config_values={})
    wire.calls.clear()
    session.send_request("projects", "get_all")

    headers = wire.calls[-1]["headers"]
    assert headers["Authorization"] == f"Bearer {session.token}"
    assert headers["X-CairnML-Worker"] == session.worker
    # the SDK adds the server's version to the client string once it has signed in
    assert f"api-{__version__}" in headers["X-CairnML-Agent"]


def test_the_trains_header_names_are_no_longer_sent(monkeypatch, tmp_path, wire):
    """Characterised as sent before the swap. The SDK sends its own names only; the server reads none of them."""
    session = make_session(monkeypatch, tmp_path, host=HOST)
    wire.calls.clear()
    session.send_request("projects", "get_all")

    headers = wire.calls[-1]["headers"]
    assert "X-Trains-Worker" not in headers
    assert "X-Trains-Agent" not in headers


def test_a_version_less_request_has_no_version_in_its_url(monkeypatch, tmp_path, wire):
    session = make_session(monkeypatch, tmp_path, host=HOST)
    wire.calls.clear()
    session.send_request("projects", "get_all")
    assert wire.calls[-1]["url"] == f"{HOST}/projects.get_all"


def test_a_versioned_request_puts_the_version_in_its_url(monkeypatch, tmp_path, wire):
    session = make_session(monkeypatch, tmp_path, host=HOST)
    wire.calls.clear()
    session.send_request("users", "get_current_user", version="2.1")
    assert wire.calls[-1]["url"] == f"{HOST}/v2.1/users.get_current_user"


# ---- the start-up sign-in: connection retries ---------------------------------------------------------------------


def test_the_start_up_sign_in_tries_to_connect_four_times_then_the_configured_policy_applies(
    monkeypatch, tmp_path, wire
):
    seen = sign_in_recorder(monkeypatch)
    session = make_session(monkeypatch, tmp_path, host=HOST)
    assert seen == [4]
    assert session._Session__http_session.get_adapter(HOST).max_retries.connect == load().get(
        "api.http.retries.connect"
    )


def test_the_start_up_override_can_be_switched_off(monkeypatch, tmp_path, wire):
    monkeypatch.setenv("CAIRNML_AGENT_INITIAL_CONNECT_RETRY_OVERRIDE", "false")
    seen = sign_in_recorder(monkeypatch)
    make_session(monkeypatch, tmp_path, host=HOST)
    assert seen == [load().get("api.http.retries.connect")]


def test_the_start_up_override_takes_a_number(monkeypatch, tmp_path, wire):
    monkeypatch.setenv("CAIRNML_AGENT_INITIAL_CONNECT_RETRY_OVERRIDE", "7")
    seen = sign_in_recorder(monkeypatch)
    make_session(monkeypatch, tmp_path, host=HOST)
    assert seen == [7]


def test_the_execute_command_keeps_the_configured_connect_retries_at_start_up(monkeypatch, tmp_path, wire):
    seen = sign_in_recorder(monkeypatch)
    make_session(monkeypatch, tmp_path, host=HOST, command="execute")
    assert seen == [load().get("api.http.retries.connect")]


# ---- the server's API level and feature set, from the token -------------------------------------------------------


def test_the_server_api_level_is_read_from_the_token(monkeypatch, tmp_path, wire):
    make_session(monkeypatch, tmp_path, host=HOST, auth_token=token(api_version="2.9", feature_set="basic"))
    # the SDK keeps the server's level in max_api_version; api_version is the level the SDK's client is generated for
    assert Session.max_api_version == "2.9"
    assert Session.feature_set == "basic"


def test_a_feature_checks_compare_against_the_server_api_level(monkeypatch, tmp_path, wire):
    make_session(monkeypatch, tmp_path, host=HOST, auth_token=token(api_version="2.9", feature_set="basic"))
    assert Session.check_min_api_version("2.8") is True
    assert Session.check_min_api_version("2.13") is False


def test_a_newer_server_passes_the_feature_checks(monkeypatch, tmp_path, wire):
    make_session(monkeypatch, tmp_path, host=HOST, auth_token=token(api_version="2.30", feature_set="basic"))
    assert Session.check_min_api_version("2.20") is True


def test_the_feature_set_comes_from_the_token(monkeypatch, tmp_path, wire):
    make_session(monkeypatch, tmp_path, host=HOST, auth_token=token(api_version="2.9", feature_set="advanced"))
    assert Session.feature_set == "advanced"


def test_the_session_does_not_load_the_server_vaults_at_start_up(monkeypatch, tmp_path, wire):
    make_session(monkeypatch, tmp_path, host=HOST, auth_token=token(api_version="2.20", feature_set="advanced"))
    assert [c["url"] for c in wire.calls if "get_vaults" in c["url"]] == []


def test_the_forced_api_version_switch_is_dropped(monkeypatch, tmp_path, wire):
    """Characterised as honoured before the swap (CAIRNML_FORCE_MAX_API_VERSION set the server level). The SDK has no
    forced version by design, so the switch is dropped: the server's own level stands."""
    monkeypatch.setenv("CAIRNML_FORCE_MAX_API_VERSION", "2.20")
    make_session(monkeypatch, tmp_path, host=HOST, auth_token=token(api_version="2.30", feature_set="basic"))
    assert not hasattr(Session, "force_max_api_version")
    assert Session.max_api_version == "2.30"


# ---- sign-in rules (which credential is used) ---------------------------------------------------------------------


def test_with_no_device_key_or_token_the_refusal_names_the_agent_command(monkeypatch, tmp_path, wire):
    session = make_session(monkeypatch, tmp_path, host=HOST)
    monkeypatch.delenv("CAIRNML_AUTH_TOKEN", raising=False)
    session._Session__auth_token = None
    session._Session__job_token = None
    session._Session__job_token_file = None
    session._Session__device_id = None
    with pytest.raises(LoginError, match="cairnml-agent init"):
        session._do_refresh_token(None)


def test_the_job_token_file_is_read_again_on_every_refresh(monkeypatch, tmp_path, wire):
    job_file = tmp_path / "job.token"
    job_file.write_text("first")
    session = make_session(monkeypatch, tmp_path, host=HOST)
    session._Session__job_token_file = str(job_file)
    assert session._do_refresh_token(None) == "first"
    job_file.write_text("second")
    assert session._do_refresh_token(None) == "second"


def test_a_device_exchange_is_sent_at_version_2_8_and_signed_for_the_host(monkeypatch, tmp_path, wire):
    key_path = tmp_path / "device.key"
    key = Ed25519PrivateKey.generate()
    save_device_key(key_path, key)
    config = load()
    config.put("api.idp.device_id", "dev_agent")
    monkeypatch.delenv("CAIRNML_AUTH_TOKEN", raising=False)
    granted = token(api_version="2.9")
    wire.reply = Reply(200, {"data": {"access_token": granted}})
    with mock.patch.object(agent_device, "DEVICE_KEY_PATH", key_path):
        session = Session(config=config, host=HOST)

    exchange = [c for c in wire.calls if c["url"].endswith("devices.exchange")]
    assert exchange and exchange[0]["url"] == f"{HOST}/v2.8/devices.exchange"
    claims = jwt.decode(
        exchange[0]["json"]["assertion"],
        key.public_key(),
        algorithms=["EdDSA"],
        audience=HOST,
        options={"verify_iat": False},
    )
    assert claims["iss"] == "dev_agent"
    assert session.token == granted


def test_a_device_key_read_from_the_agent_folder_is_the_key_the_exchange_uses(monkeypatch, tmp_path, wire):
    """The agent's own key path is read when the session is made, not the SDK's default key."""
    key_path = tmp_path / "agent" / "device.key"
    secure_folder(key_path.parent)
    save_device_key(key_path, Ed25519PrivateKey.generate())
    config = load()
    config.put("api.idp.device_id", "dev_agent")
    monkeypatch.delenv("CAIRNML_AUTH_TOKEN", raising=False)
    wire.reply = Reply(200, {"data": {"access_token": token(api_version="2.9")}})
    with mock.patch.object(agent_device, "DEVICE_KEY_PATH", key_path):
        Session(config=config, host=HOST)
    assert wire.calls[0]["url"].endswith("devices.exchange")


def test_a_refused_device_exchange_is_a_login_error_with_the_server_reason(monkeypatch, tmp_path, wire):
    key_path = tmp_path / "device.key"
    save_device_key(key_path, Ed25519PrivateKey.generate())
    config = load()
    config.put("api.idp.device_id", "dev_agent")
    monkeypatch.delenv("CAIRNML_AUTH_TOKEN", raising=False)
    wire.reply = Reply(403, {"meta": {"result_msg": "device revoked"}})
    with (
        mock.patch.object(agent_device, "DEVICE_KEY_PATH", key_path),
        pytest.raises(LoginError, match="device revoked"),
    ):
        Session(config=config, host=HOST)


# ---- after start-up: the session keeps going over transport errors --------------------------------------------------


def test_the_sdk_offline_switch_does_not_stop_the_agent(monkeypatch, tmp_path, wire):
    """The agent never had an offline mode; the SDK's switch is kept from it."""
    monkeypatch.setattr(SdkSession, "_offline_mode", True, raising=False)
    session = make_session(monkeypatch, tmp_path, host=HOST)
    wire.calls.clear()
    reply = session.send_request("projects", "get_all")
    assert reply is not None
    assert len(wire.calls) == 1


def test_after_start_up_a_dropped_connection_is_retried_until_it_answers(monkeypatch, tmp_path, wire):
    session = make_session(monkeypatch, tmp_path, host=HOST)
    wire.calls.clear()
    wire.errors = [requests.ConnectionError("connection reset")]
    with mock.patch("time.sleep"):
        reply = session.send_request("projects", "get_all")
    assert reply.status_code == 200
    assert len(wire.calls) == 2

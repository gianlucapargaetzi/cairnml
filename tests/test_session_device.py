import os
import time
from unittest import mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pyhocon import ConfigFactory

from cairnml.backend_api.services import API_LEVEL
from cairnml.backend_api.session.session import LoginError, Session
from cairnml.idp.device import device_assertion, save_device_key

HOST = "https://api.example"
DEVICE = "dev_laptop"


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.reason = "x"

    def json(self):
        return self._body


def device_session(device_id=DEVICE, job_token=None):
    session = Session.__new__(Session)
    session._Session__host = HOST
    session._Session__device_id = device_id
    session._Session__device_key_path = None
    session._Session__job_token = job_token
    session._Session__job_token_file = None
    session._Session__auth_token = None
    session._Session__access_key = None
    session._Session__secret_key = None
    session._verbose = False
    return session


@pytest.fixture
def key_file(tmp_path):
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "device.key"
    save_device_key(path, key)
    return key, path


def test_a_job_token_is_used_as_it_is_and_never_exchanged():
    session = device_session(job_token="job-token-1")
    with mock.patch.object(Session, "_send_request") as send:
        assert session._do_refresh_token(None) == "job-token-1"
    send.assert_not_called()


def test_a_device_exchanges_a_signed_assertion_for_a_token(key_file):
    key, path = key_file
    session = device_session()
    reply = Response(200, {"data": {"access_token": "access-1", "expires_in": 900}})
    with (
        mock.patch("cairnml.idp.device.DEVICE_KEY_PATH", path),
        mock.patch.object(Session, "_send_request", return_value=reply) as send,
    ):
        assert session._do_refresh_token(None) == "access-1"

    kwargs = send.call_args.kwargs
    assert (kwargs["service"], kwargs["action"], kwargs["version"]) == ("devices", "exchange", API_LEVEL)  # the level, not the label of the definition (2.8)
    claims = jwt.decode(
        kwargs["json"]["assertion"],
        key.public_key(),
        algorithms=["EdDSA"],
        audience=HOST,
        options={"verify_iat": False},
    )
    assert claims["iss"] == DEVICE and claims["sub"] == DEVICE
    assert claims["exp"] - claims["iat"] <= 60


def test_a_refused_exchange_is_a_login_error(key_file):
    _, path = key_file
    session = device_session()
    refused = Response(401, {"meta": {"result_msg": "device revoked"}})
    with (
        mock.patch("cairnml.idp.device.DEVICE_KEY_PATH", path),
        mock.patch.object(Session, "_send_request", return_value=refused),
    ):
        with pytest.raises(LoginError, match="device revoked"):
            session._do_refresh_token(None)


def test_the_device_key_file_is_owner_only(tmp_path):
    path = tmp_path / "device.key"
    save_device_key(path, Ed25519PrivateKey.generate())
    assert os.stat(path).st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        save_device_key(path, Ed25519PrivateKey.generate())


def test_the_assertion_helper_reads_the_key_and_targets_the_host(key_file):
    key, path = key_file
    token = device_assertion(DEVICE, HOST, key_path=path)
    claims = jwt.decode(token, key.public_key(), algorithms=["EdDSA"], audience=HOST, options={"verify_iat": False})
    assert claims["iss"] == DEVICE
    assert claims["exp"] > time.time()


def test_the_session_signs_with_the_key_at_the_path_it_was_given(tmp_path, key_file):
    key, path = key_file
    session = device_session()
    session._Session__device_key_path = str(path)
    reply = Response(200, {"data": {"access_token": "access-1", "expires_in": 900}})
    # the default key file does not exist: a session that ignored the path would fail
    with (
        mock.patch("cairnml.idp.device.DEVICE_KEY_PATH", tmp_path / "missing.key"),
        mock.patch.object(Session, "_send_request", return_value=reply) as send,
    ):
        assert session._do_refresh_token(None) == "access-1"
    claims = jwt.decode(
        send.call_args.kwargs["json"]["assertion"],
        key.public_key(),
        algorithms=["EdDSA"],
        audience=HOST,
        options={"verify_iat": False},
    )
    assert claims["iss"] == DEVICE


def token_reply():
    token = jwt.encode({"exp": int(time.time()) + 900}, "test", algorithm="HS256")
    return token, Response(200, {"data": {"access_token": token, "expires_in": 900}})


def test_the_key_path_from_the_config_is_used_and_expanded(tmp_path, monkeypatch, key_file):
    key, _ = key_file
    monkeypatch.setenv("HOME", str(tmp_path))
    save_device_key(tmp_path / "home-device.key", key)
    token, reply = token_reply()
    config = ConfigFactory.from_dict(
        {
            "api": {
                "http": {"max_req_size": 1000000},
                "idp": {"device_id": DEVICE, "device_key_path": "~/home-device.key"},
            }
        }
    )
    # the default key file must not be usable, or a session that ignored the config would pass
    with (
        mock.patch("cairnml.idp.device.DEVICE_KEY_PATH", tmp_path / "missing.key"),
        mock.patch.object(Session, "_send_request", return_value=reply),
    ):
        session = Session(host=HOST, config=config)
        assert session._do_refresh_token(None) == token


def test_the_key_path_argument_wins_over_the_config(tmp_path, key_file):
    _, path = key_file
    token, reply = token_reply()
    config = ConfigFactory.from_dict(
        {
            "api": {
                "http": {"max_req_size": 1000000},
                "idp": {"device_id": DEVICE, "device_key_path": str(tmp_path / "wrong.key")},
            }
        }
    )
    with (
        mock.patch("cairnml.idp.device.DEVICE_KEY_PATH", tmp_path / "missing.key"),
        mock.patch.object(Session, "_send_request", return_value=reply),
    ):
        session = Session(host=HOST, device_key_path=str(path), config=config)
        assert session._do_refresh_token(None) == token

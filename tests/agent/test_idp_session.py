import os
from unittest import mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from clearml_agent.backend_api.session.session import LoginError, Session
from clearml_agent.idp.device import device_assertion, save_device_key

HOST = "https://api.example"
DEVICE = "dev_agent"


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.reason = "x"

    def json(self):
        return self._body


def agent_session(device_id=DEVICE, job_token=None):
    session = Session.__new__(Session)
    session._Session__host = HOST
    session._Session__device_id = device_id
    session._Session__job_token = job_token
    session._Session__job_token_file = None
    session._verbose = False
    return session


@pytest.fixture
def key_file(tmp_path):
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "device.key"
    save_device_key(path, key)
    return key, path


def test_a_job_token_is_used_as_it_is_and_never_exchanged():
    session = agent_session(job_token="job-token-1")
    with mock.patch.object(Session, "_send_request") as send:
        assert session._do_refresh_token(None) == "job-token-1"
    send.assert_not_called()


def test_an_agent_exchanges_a_signed_assertion_for_a_token(key_file):
    key, path = key_file
    session = agent_session()
    reply = Response(200, {"data": {"access_token": "access-1", "expires_in": 900}})
    with mock.patch("clearml_agent.idp.device.DEVICE_KEY_PATH", path), \
            mock.patch.object(Session, "_send_request", return_value=reply) as send:
        assert session._do_refresh_token(None) == "access-1"

    kwargs = send.call_args.kwargs
    assert (kwargs["service"], kwargs["action"], kwargs["version"]) == ("devices", "exchange", "2.8")
    claims = jwt.decode(kwargs["json"]["assertion"], key.public_key(), algorithms=["EdDSA"],
                        audience=HOST, options={"verify_iat": False})
    assert claims["iss"] == DEVICE


def test_a_refused_exchange_is_a_login_error(key_file):
    _, path = key_file
    session = agent_session()
    refused = Response(403, {"meta": {"result_msg": "device revoked"}})
    with mock.patch("clearml_agent.idp.device.DEVICE_KEY_PATH", path), \
            mock.patch.object(Session, "_send_request", return_value=refused):
        with pytest.raises(LoginError, match="device revoked"):
            session._do_refresh_token(None)


def test_the_auth_token_given_in_the_environment_is_kept():
    session = agent_session(device_id=None)
    assert session._do_refresh_token("given-token") == "given-token"


def test_the_device_key_file_is_owner_only(tmp_path):
    path = tmp_path / "device.key"
    save_device_key(path, Ed25519PrivateKey.generate())
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_the_assertion_helper_targets_the_host(key_file):
    key, path = key_file
    token = device_assertion(DEVICE, HOST, key_path=path)
    claims = jwt.decode(token, key.public_key(), algorithms=["EdDSA"], audience=HOST,
                        options={"verify_iat": False})
    assert claims["iss"] == DEVICE

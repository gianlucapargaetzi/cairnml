import re
from pathlib import Path
from unittest import mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cairnml.backend_api.services import API_LEVEL, resolve
from cairnml.backend_api.services import devices as devices_api
from cairnml.backend_api.session import Session
from cairnml.cli import devices as cli_devices
from cairnml.cli import doctor as cli_doctor
from cairnml.idp import enrol as enrol_module
from cairnml.idp.enrol import EnrolmentFailed, enrol, enrol_device, revoke_device
from cairnml.idp.device import save_device_key
from cairnml.idp.keys import load_public_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PUBLIC = "http://cairnml:8008"
API = "http://localhost:8008"  # the address typed on the command line; the server's own address wins


def endpoint(request_cls, server=PUBLIC):
    """The URL of a generated request class, at the negotiated API level (not the label of the class, which is the
    version its definition began at: devices.* are 2.8, and a client asks for the level it was generated for)."""
    return f"{server}/v{API_LEVEL}/{request_cls._service}.{request_cls._action}"


def token_for(user):
    return jwt.encode({"preferred_username": user}, "not-checked", algorithm="HS256")


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeServer:
    def __init__(self, enrol_reply=(200, {"data": {"id": "dev_1", "kind": "client"}}), revoke_reply=(200, {"data": {}}),
                 exchange_reply=(200, {"data": {"access_token": "device-token"}})):
        self.enrol_reply = enrol_reply
        self.revoke_reply = revoke_reply
        self.exchange_reply = exchange_reply
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        if url.endswith("devices.idp_settings"):
            return Response(200, {"data": {"issuer": "https://idp.example/realms/mlops",
                                           "sdk_client_id": "cairnml-sdk",
                                           "public_url": PUBLIC, "web_url": "http://cairnml:8080"}})
        if url.endswith("devices.exchange"):
            return Response(*self.exchange_reply)
        if url.endswith("devices.revoke"):
            return Response(*self.revoke_reply)
        if url.endswith("users.get_current_user"):
            return Response(200, {"data": {"user": {"username": "alice"}}})
        return Response(*self.enrol_reply)


def login(issuer, client_id, **kwargs):
    assert (issuer, client_id) == ("https://idp.example/realms/mlops", "cairnml-sdk")
    return token_for("alice")


def test_enrolment_uses_the_servers_public_address_and_registers_the_key(tmp_path):
    server = FakeServer()
    key_path = tmp_path / "device.key"
    result = enrol(API, http=server, login=login, key_path=key_path, name="laptop")

    assert result.device_id == "dev_1"
    assert result.server_url == PUBLIC
    assert result.web_url == "http://cairnml:8080"
    assert result.user == "alice"
    url, body, headers = server.calls[-1]
    assert url == endpoint(devices_api.EnrollRequest)
    assert headers == {"Authorization": "Bearer " + token_for("alice")}
    assert body["kind"] == "client" and body["name"] == "laptop"
    public: Ed25519PublicKey = load_public_key(body["public_key"])
    assert public is not None
    assert key_path.exists()


def test_an_enrolment_the_server_accepts_is_refused_without_force(tmp_path):
    key_path = tmp_path / "device.key"
    save_device_key(key_path, Ed25519PrivateKey.generate())
    before = key_path.read_bytes()
    server = FakeServer()
    with pytest.raises(EnrolmentFailed, match="already enrolled") as info:
        enrol(API, http=server, login=login, key_path=key_path, previous_device_id="dev_old")
    assert info.value.status is None
    assert key_path.read_bytes() == before
    assert not any(url.endswith("devices.enroll") for url, _, _ in server.calls)


def test_a_stale_enrolment_the_server_refuses_is_replaced(tmp_path):
    """A key and device id from an earlier server, which this server does not accept: enrol again."""
    key_path = tmp_path / "device.key"
    key_path.write_text("old key")
    server = FakeServer(exchange_reply=(401, {"meta": {"result_msg": "unknown device"}}))
    result = enrol(API, http=server, login=login, key_path=key_path, previous_device_id="dev_old")
    assert result.device_id == "dev_1"
    assert key_path.read_text() != "old key"


def test_an_enrolment_without_a_device_id_is_stale(tmp_path):
    key_path = tmp_path / "device.key"
    key_path.write_text("old key")
    server = FakeServer()
    enrol(API, http=server, login=login, key_path=key_path)
    assert key_path.read_text() != "old key"


def test_an_enrolment_that_cannot_be_checked_is_not_called_enrolled(tmp_path):
    key_path = tmp_path / "device.key"
    save_device_key(key_path, Ed25519PrivateKey.generate())
    before = key_path.read_bytes()
    server = FakeServer(exchange_reply=(503, {"meta": {"result_msg": "down"}}))
    with pytest.raises(EnrolmentFailed, match="cannot check the existing enrolment"):
        enrol(API, http=server, login=login, key_path=key_path, previous_device_id="dev_old")
    assert key_path.read_bytes() == before


def test_force_revokes_the_previous_device_first(tmp_path):
    key_path = tmp_path / "device.key"
    save_device_key(key_path, Ed25519PrivateKey.generate())
    server = FakeServer()
    result = enrol(API, http=server, login=login, key_path=key_path, force=True, previous_device_id="dev_old")
    assert result.device_id == "dev_1"
    revoke = [body for url, body, _ in server.calls if url.endswith("devices.revoke")]
    assert revoke == [{"id": "dev_old"}]


def test_a_refused_enrolment_leaves_no_key_behind(tmp_path):
    key_path = tmp_path / "device.key"
    server = FakeServer(enrol_reply=(403, {"meta": {"result_msg": "only an admin may enrol an agent"}}))
    with pytest.raises(EnrolmentFailed, match="admin"):
        enrol(API, http=server, login=login, key_path=key_path)
    assert not key_path.exists()


def test_an_agent_is_enrolled_as_an_agent(tmp_path):
    server = FakeServer()
    device_id = enrol_device(API, kind="agent", http=server, login=login, key_path=tmp_path / "device.key")
    assert device_id == "dev_1"
    _, body, _ = server.calls[-1]
    assert body["kind"] == "agent"


def test_sign_out_revokes_the_device_with_its_own_key(tmp_path):
    key_path = Path(tmp_path / "device.key")
    save_device_key(key_path, Ed25519PrivateKey.generate())
    server = FakeServer()
    revoke_device(PUBLIC, "dev_1", key_path=key_path, http=server)
    url, body, headers = server.calls[-1]
    assert url == endpoint(devices_api.RevokeRequest)
    assert body == {"id": "dev_1"}
    assert headers == {"Authorization": "Bearer device-token"}


def test_a_wrong_address_says_which_address_to_give(tmp_path):
    server = FakeServer()
    server.post = lambda url, json=None, headers=None, timeout=None: Response(405, {})
    with pytest.raises(EnrolmentFailed, match="give the address of the server"):
        enrol(API, http=server, login=login, key_path=tmp_path / "device.key")


def test_the_key_folder_is_created_for_its_owner_only(tmp_path):
    import stat

    from cairnml.idp.device import secure_folder

    folder = tmp_path / "a" / ".cairnml"
    secure_folder(folder)
    assert folder.is_dir()
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_each_call_takes_its_version_from_the_negotiated_level(tmp_path, monkeypatch):
    """The version on the wire is the negotiated level, read from Session, not written here and not the label of the
    request class: a server that reports a lower level moves every call to that level."""
    monkeypatch.setattr(Session, "max_api_version", "2.30")
    key_path = tmp_path / "device.key"
    save_device_key(key_path, Ed25519PrivateKey.generate())
    server = FakeServer()
    enrol(API, http=server, login=login, key_path=tmp_path / "new.key")
    revoke_device(PUBLIC, "dev_1", key_path=key_path, http=server)
    enrol_module.whoami(PUBLIC, "dev_1", key_path=key_path, http=server)
    urls = [url for url, _, _ in server.calls]
    assert urls == [
        API + "/v2.30/devices.idp_settings",
        PUBLIC + "/v2.30/devices.enroll",
        PUBLIC + "/v2.30/devices.exchange",
        PUBLIC + "/v2.30/devices.revoke",
        PUBLIC + "/v2.30/devices.exchange",
        PUBLIC + "/v2.30/users.get_current_user",
    ]


@pytest.mark.parametrize("request_cls", [devices_api.IdpSettingsRequest, devices_api.ExchangeRequest,
                                         devices_api.RevokeRequest, devices_api.EnrollRequest,
                                         devices_api.GetAllRequest])
def test_the_enrolment_calls_use_the_same_definition_at_every_level_they_are_sent_at(request_cls):
    """The devices.* definitions began at 2.8 and have not changed: the generated class is the same one at the
    SDK's level and at a lower server level, so sending at the level does not change what they mean. (users.get_current_user
    is not in this list: it changed at 2.31, so a server below that serves its older definition.)"""
    for level in ("2.41", "2.30", "2.12"):
        assert resolve(request_cls._service, request_cls._action, level)[0] is request_cls


def test_no_hand_written_call_carries_an_api_version_literal():
    """A version written into a URL in the modules that talk to the server is the defect of #117: the generated classes own it."""
    for module in (enrol_module, cli_devices, cli_doctor):
        source = Path(module.__file__).read_text()
        assert re.findall(r"""["'/]v?\d+\.\d+""", source) == [], module.__name__

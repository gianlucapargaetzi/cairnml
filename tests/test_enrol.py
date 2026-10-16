import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from clearml.idp.device import DEVICE_KEY_PATH
from clearml.idp.enrol import EnrolmentFailed, enrol_device
from clearml.idp.keys import load_public_key

API = "https://api.example"


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeServer:
    def __init__(self, enrol_reply=(200, {"data": {"id": "dev_1", "kind": "client"}})):
        self.enrol_reply = enrol_reply
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        if url.endswith("devices.idp_settings"):
            return Response(200, {"data": {"issuer": "https://idp.example/realms/mlops", "sdk_client_id": "clearml-sdk"}})
        return Response(*self.enrol_reply)


def login(issuer, client_id, **kwargs):
    assert (issuer, client_id) == ("https://idp.example/realms/mlops", "clearml-sdk")
    return "idp-token"


def test_enrolment_registers_the_public_key_under_the_sign_in(tmp_path):
    server = FakeServer()
    key_path = tmp_path / "device.key"
    device_id = enrol_device(API, http=server, login=login, key_path=key_path, name="laptop")

    assert device_id == "dev_1"
    url, body, headers = server.calls[-1]
    assert url == API + "/v2.8/devices.enroll"
    assert headers == {"Authorization": "Bearer idp-token"}
    assert body["kind"] == "client" and body["name"] == "laptop"
    public: Ed25519PublicKey = load_public_key(body["public_key"])
    assert public is not None
    assert key_path.exists()


def test_an_enrolled_machine_is_not_enrolled_again(tmp_path):
    key_path = tmp_path / "device.key"
    key_path.write_text("existing")
    server = FakeServer()
    with pytest.raises(EnrolmentFailed, match="already"):
        enrol_device(API, http=server, login=login, key_path=key_path)
    assert key_path.read_text() == "existing"
    assert not any(url.endswith("devices.enroll") for url, _, _ in server.calls)


def test_a_refused_enrolment_leaves_no_key_behind(tmp_path):
    key_path = tmp_path / "device.key"
    server = FakeServer(enrol_reply=(403, {"meta": {"result_msg": "only an admin may enrol an agent"}}))
    with pytest.raises(EnrolmentFailed, match="admin"):
        enrol_device(API, http=server, login=login, key_path=key_path)
    assert not key_path.exists()

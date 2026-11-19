"""The bodies the SDK sends.

Generated requests send exactly the fields of their schema (no HTTP verb as a field), and the hand-written callers
send the bodies those requests produce, equal to the bodies they sent before the callers used the generated classes.
"""

import importlib
import pkgutil
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cairnml.backend_api import services
from cairnml.backend_api.session import Request
from cairnml.backend_api.session.request import CompoundRequest
from cairnml.cli import devices as cli_devices
from cairnml.cli import doctor as cli_doctor
from cairnml.datasets import credentials
from cairnml.idp import enrol as idp_enrol
from cairnml.idp.device import save_device_key

PUBLIC = "http://cairnml:8008"


def _generated_request_classes():
    found = []
    for info in pkgutil.iter_modules(services.__path__):
        if info.ispkg:
            continue  # legacy generations are packages of their own and are not called by the SDK
        module = importlib.import_module(f"{services.__name__}.{info.name}")
        for obj in vars(module).values():
            if isinstance(obj, type) and issubclass(obj, Request) and obj.__module__ == module.__name__:
                if obj._service is not None:
                    found.append(obj)
    return found


GENERATED = _generated_request_classes()


def test_the_generated_classes_were_found():
    assert services.queues.GetAllRequest in GENERATED
    assert services.events.AddRequest in GENERATED


@pytest.mark.parametrize("request_cls", GENERATED, ids=lambda c: f"{c._service}.{c._action}.{c.__name__}")
def test_a_generated_request_has_a_field_for_each_schema_property_and_no_other(request_cls):
    fields = set(request_cls._get_data_props().values())
    if issubclass(request_cls, CompoundRequest):
        fields.discard(request_cls._item_prop_name)  # the item is sent as the body itself, not under its own name
    schema_properties = set((request_cls._schema or {}).get("properties", {}))
    assert fields == schema_properties


def test_an_idp_settings_request_sends_an_empty_body():
    assert services.devices.IdpSettingsRequest().to_dict() == {}


def test_a_request_with_fields_sends_only_those_fields():
    assert services.devices.RevokeRequest(id="dev_1").to_dict() == {"id": "dev_1"}
    assert services.datachunks.DatasetCredentialsRequest(repository="o/r", level="read").to_dict() == {
        "repository": "o/r",
        "level": "read",
    }


# Hand-written callers: the bodies they send, pinned before they are built from the generated classes.


class Reply:
    def __init__(self, data=None, ok=True):
        self.ok = ok
        self.status_code = 200 if ok else 400
        self.text = ""
        self._data = data or {}

    def json(self):
        meta = {"id": "i", "trx": "t", "result_code": 200, "result_subcode": 0, "result_msg": "OK", "error_stack": ""}
        return {"meta": meta, "data": self._data}


class FakeSession:
    """Records what the SDK would send through send_request, and answers with the given data."""

    def __init__(self, data=None):
        self.calls = []
        self.data = data or {}

    def send_request(self, **kwargs):
        self.calls.append(kwargs)
        return Reply(self.data)

    @property
    def body(self):
        return self.calls[-1]["json"]


DEVICES = [{"id": "dev_1", "name": "laptop", "user": "usr_alice", "kind": "client", "revoked": False}]
REPLIES = {
    "devices.idp_settings": {
        "issuer": "https://idp.example/realms/mlops",
        "sdk_client_id": "cairnml-sdk",
        "public_url": PUBLIC,
        "web_url": "",
    },
    "devices.exchange": {"access_token": "device-token"},
    "devices.revoke": {},
    "devices.enroll": {"id": "dev_2", "notice": "", "queue": ""},
    "devices.get_all": {"devices": DEVICES},
    "users.get_current_user": {"user": {"username": "alice"}},
    "queues.get_all": {"queues": []},
    "workers.get_all": {"workers": []},
}


class FakeHttp:
    """The HTTP session of the enrolment helpers: records each POST by its endpoint and answers from REPLIES."""

    def __init__(self):
        self.posts = []

    def post(self, url, json=None, headers=None, timeout=None):
        endpoint = url.rsplit("/", 1)[-1]
        self.posts.append((endpoint, json))
        return _HttpReply(REPLIES.get(endpoint, {}))

    def body_of(self, endpoint):
        return [body for name, body in self.posts if name == endpoint]


class _HttpReply:
    status_code = 200
    text = ""

    def __init__(self, data):
        self._data = data

    def json(self):
        meta = {"id": "i", "trx": "t", "result_code": 200, "result_subcode": 0, "result_msg": "OK", "error_stack": ""}
        return {"meta": meta, "data": self._data}


@pytest.fixture
def key_path(tmp_path) -> Path:
    path = tmp_path / "device.key"
    save_device_key(path, Ed25519PrivateKey.generate())
    return path


def test_the_idp_settings_call_sends_an_empty_body():
    http = FakeHttp()
    idp_enrol._settings(http, PUBLIC)
    assert http.body_of("devices.idp_settings") == [{}]


def test_the_device_exchange_sends_the_assertion_only(key_path):
    http = FakeHttp()
    idp_enrol._token_for_device(http, PUBLIC, "dev_1", key_path)
    (body,) = http.body_of("devices.exchange")
    assert set(body) == {"assertion"}


def test_revoking_a_device_sends_its_id(key_path):
    http = FakeHttp()
    idp_enrol.revoke_device(PUBLIC, "dev_1", key_path=key_path, http=http)
    assert http.body_of("devices.revoke") == [{"id": "dev_1"}]


def test_whoami_sends_an_empty_body(key_path):
    http = FakeHttp()
    assert idp_enrol.whoami(PUBLIC, "dev_1", key_path=key_path, http=http) == {"username": "alice"}
    assert http.body_of("users.get_current_user") == [{}]


def test_enrolling_sends_the_kind_the_name_and_the_public_key(tmp_path):
    http = FakeHttp()
    token = jwt.encode({"preferred_username": "alice"}, "not-checked", algorithm="HS256")
    result = idp_enrol.enrol(
        PUBLIC,
        name="laptop",
        http=http,
        login=lambda issuer, client_id, **kwargs: token,
        key_path=tmp_path / "device.key",
    )
    assert result.device_id == "dev_2"
    (body,) = http.body_of("devices.enroll")
    assert set(body) == {"kind", "name", "public_key"}
    assert (body["kind"], body["name"]) == ("client", "laptop")
    assert body["public_key"].startswith("-----BEGIN PUBLIC KEY-----")


def test_the_device_list_sends_an_empty_body(key_path):
    http = FakeHttp()
    assert cli_devices.list_devices(PUBLIC, "dev_1", key_path=key_path, http=http) == DEVICES
    assert http.body_of("devices.get_all") == [{}]


def test_the_device_revoke_command_sends_the_target_id(key_path):
    http = FakeHttp()
    cli_devices.revoke_other_device(PUBLIC, "dev_1", "dev_9", key_path=key_path, http=http)
    assert http.body_of("devices.revoke") == [{"id": "dev_9"}]


def test_the_queue_check_asks_for_the_queues_with_the_servers_default_visibility(key_path):
    # queues.get_all defaults search_hidden to false in the schema, the same as the server does for an empty body
    http = FakeHttp()
    sign_in = cli_doctor.Check("sign-in", True, "signed in")
    cli_doctor.check_queues(PUBLIC, "dev_1", sign_in, key_path, http=http)
    assert http.body_of("queues.get_all") == [{"search_hidden": False}]


def test_the_queue_check_asks_for_the_workers_of_the_servers_default_window(key_path):
    # workers.get_all defaults last_seen to 3600 seconds in the schema, the same window the server applies to an empty body
    http = FakeHttp()
    sign_in = cli_doctor.Check("sign-in", True, "signed in")
    cli_doctor.check_queues(PUBLIC, "dev_1", sign_in, key_path, http=http)
    assert http.body_of("workers.get_all") == [{"last_seen": 3600}]


def test_the_storage_keys_request_sends_the_repository_and_the_level():
    session = FakeSession(
        {
            "endpoint": "e",
            "region": "r",
            "bucket": "b",
            "prefix": "p",
            "access_key": "a",
            "secret_key": "s",
            "session_token": "t",
            "expires_at": "x",
        }
    )
    credentials.fetch_storage_keys("owner/name", "read", session=session)
    assert session.body == {"repository": "owner/name", "level": "read"}


def test_the_git_token_request_of_a_job_sends_the_task_and_the_repository(monkeypatch):
    monkeypatch.setenv("CAIRNML_JOB_TOKEN", "job")
    session = FakeSession({"token": "tok"})
    assert credentials.fetch_git_token("owner/name", "task-1", session=session) == "tok"
    assert session.body == {"task": "task-1", "repository": "owner/name"}


def test_the_repository_list_sends_an_empty_body():
    session = FakeSession({"repositories": []})
    assert credentials.fetch_repository_list(session=session) == []
    assert session.body == {}


def test_the_credential_list_sends_an_empty_body():
    session = FakeSession({"credentials": []})
    assert credentials.list_credentials(session=session) == []
    assert session.body == {}


def test_a_person_git_token_sends_the_reference_and_the_level():
    session = FakeSession({"token": "tok"})
    assert credentials.fetch_person_git_token("https://github.com/o/r", "read", session=session) == "tok"
    assert session.body == {"ref": "https://github.com/o/r", "level": "read"}


def test_listing_a_repository_sends_its_url_and_role_only():
    session = FakeSession({"repository": {"id": "r1"}})
    credentials.add_repository("https://github.com/o/r", session=session)
    assert session.body == {"url": "https://github.com/o/r", "role": "dataset"}


def test_listing_a_repository_sends_the_credential_project_and_flags_when_given():
    session = FakeSession({"repository": {"id": "r1"}})
    credentials.add_repository(
        "https://github.com/o/r", role="code", credential="c1", project="p1", shared=True, session=session
    )
    assert session.body == {
        "url": "https://github.com/o/r",
        "role": "code",
        "credential": "c1",
        "project": "p1",
        "shared": True,
    }


def test_assigning_none_needed_sends_the_flag_and_the_default_role():
    session = FakeSession({"repository": {"id": "r1"}})
    credentials.assign_credential("https://github.com/o/r", none_needed=True, session=session)
    assert session.body == {"ref": "https://github.com/o/r", "role": "dataset", "none_needed": True}


def test_assigning_a_credential_sends_its_id():
    session = FakeSession({"repository": {"id": "r1"}})
    credentials.assign_credential("https://github.com/o/r", credential="c1", session=session)
    assert session.body == {"ref": "https://github.com/o/r", "role": "dataset", "credential": "c1"}


@pytest.mark.parametrize(
    "call, body",
    [
        (lambda s: credentials.unassign_credential("R", session=s), {"ref": "R"}),
        (lambda s: credentials.check_repository("R", session=s), {"ref": "R"}),
        (lambda s: credentials.delete_repository("R", session=s), {"ref": "R"}),
        (lambda s: credentials.resolve_dataset("R", session=s), {"ref": "R"}),
        (lambda s: credentials.link_repository("R", "P", session=s), {"ref": "R", "project": "P"}),
        (lambda s: credentials.unlink_repository("R", "P", session=s), {"ref": "R", "project": "P"}),
    ],
)
def test_the_repository_actions_send_the_reference_and_project(call, body):
    session = FakeSession({"repository": {"id": "r1"}})
    call(session)
    assert session.body == body


def test_sharing_sends_the_flag_even_when_it_is_false():
    session = FakeSession({"repository": {"id": "r1"}})
    credentials.set_repository_shared("R", False, session=session)
    assert session.body == {"ref": "R", "shared": False}
    credentials.set_repository_shared("R", True, credential="c1", session=session)
    assert session.body == {"ref": "R", "shared": True, "credential": "c1"}

"""The calls that used to build their body by hand, or name their service and action as strings, now send the generated
request class of their action. Each test reads what was sent, as a request object, and what it sends on the wire.
"""

import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import cairnml.storage.helper as helper
from cairnml import Task
from cairnml.backend_api.services import datachunks, serving
from cairnml.backend_api.session import defs
from cairnml.backend_api.session.callresult import CallResult
from cairnml.backend_api.session.response import ResponseMeta
from cairnml.backend_interface.base import InterfaceBase
from cairnml.router.endpoint_telemetry import EndpointTelemetry
from cairnml.router.router import HttpRouter
from cairnml.storage.helper import DATACHUNK_SCHEME, StorageError, _DatachunkDriver

REPO_ROOT = Path(__file__).resolve().parents[1]


def _answer(data=None, ok=True):
    meta = ResponseMeta.from_raw_data(status_code=200 if ok else 400, text="OK" if ok else "refused", endpoint="x.y")
    return CallResult(meta=meta, response_data=data or {})


class RecordingSession:
    """Records the request objects sent through Session.send and answers each with the data for its action."""

    def __init__(self, answers=None, ok=True):
        self.sent = []
        self.answers = answers or {}
        self.ok = ok

    def send(self, req_obj, headers=None):
        self.sent.append(req_obj)
        return _answer(self.answers.get(req_obj._action), self.ok)

    @property
    def request(self):
        return self.sent[-1]


# serving: the register and status calls of the endpoint telemetry


def _telemetry(session) -> EndpointTelemetry:
    telemetry = EndpointTelemetry.__new__(EndpointTelemetry)  # no task and no register call: the state is set here
    telemetry.session = session
    telemetry.container_info = {
        "container_id": "c1",
        "endpoint_name": "ep",
        "model_name": "m",
        "model_source": None,
        "model_version": None,
        "preprocess_artifact": None,
        "input_type": "str",
        "input_size": "None",
        "tags": {"team": "a"},
        "system_tags": ["x"],
        "endpoint_url": "http://endpoint",
        "reference": [{"type": "task", "value": "t1"}],
    }
    return telemetry


def test_registering_a_container_sends_the_generated_request_with_the_schema_fields_only():
    session = RecordingSession()

    _telemetry(session).register_container()

    (request,) = session.sent
    assert type(request) is serving.RegisterContainerRequest
    assert request.to_dict() == {
        "container_id": "c1",
        "endpoint_name": "ep",
        "model_name": "m",
        "endpoint_url": "http://endpoint",
        "input_type": "str",
        "input_size": "None",
        "reference": [{"type": "task", "value": "t1"}],
        "timeout": 600,
    }


def test_a_status_report_sends_the_generated_request_with_the_schema_fields_only():
    session = RecordingSession()
    telemetry = _telemetry(session)
    telemetry.uptime_timestamp = time.time()
    telemetry.requests_num = 3
    telemetry.requests_num_window = 2
    telemetry.requests_num_prev_window = 1
    telemetry.latency_sum_window = 10.0
    telemetry.latency_num_window = 2
    telemetry.get_machine_stats = lambda: {}

    telemetry.container_status_report()

    (request,) = session.sent
    assert type(request) is serving.ContainerStatusReportRequest
    body = request.to_dict()
    assert {"container_id", "endpoint_name", "model_name", "uptime_sec", "requests_num", "requests_min"} <= set(body)
    assert body["container_id"] == "c1"
    assert body["requests_num"] == 3
    assert body["requests_min"] == 3
    assert "tags" not in body and "system_tags" not in body


# datachunks: the object storage driver sends the generated requests


@pytest.fixture
def chunks(monkeypatch):
    session = RecordingSession(
        answers={
            "prepare_upload": {"id": "g1", "url": "https://store/object", "headers": {}, "multipart": None},
            "prepare_download": {"size": 3, "sha256": "abc", "url": "https://store/get"},
            "commit": {},
            "delete": {},
        }
    )
    monkeypatch.setattr(InterfaceBase, "_get_default_session", classmethod(lambda cls: session))
    driver = _DatachunkDriver()
    driver.session = session
    return driver


def _container(driver):
    return driver.get_container(f"{DATACHUNK_SCHEME}://dataset/task-1")


def test_deleting_a_datachunk_sends_the_delete_request(chunks):
    container = _container(chunks)
    obj = chunks._Object(container, "data/a.bin")

    assert chunks.delete_object(obj) is True

    (request,) = chunks.session.sent
    assert type(request) is datachunks.DeleteRequest
    assert request.to_dict() == {"task": "task-1", "kind": "dataset", "name": "data/a.bin"}


def test_getting_a_datachunk_sends_the_prepare_download_request(chunks):
    obj = chunks.get_object("datachunk://dataset/task-1", "data/a.bin")

    (request,) = chunks.session.sent
    assert type(request) is datachunks.PrepareDownloadRequest
    assert request.to_dict() == {"task": "task-1", "kind": "dataset", "name": "data/a.bin"}
    assert (obj.size, obj.sha256, obj.url) == (3, "abc", "https://store/get")


def test_uploading_a_small_file_asks_for_the_grant_then_commits_it(chunks, tmp_path, monkeypatch):
    source = tmp_path / "a.txt"
    source.write_bytes(b"abc")
    monkeypatch.setattr(helper.requests, "put", lambda *a, **k: SimpleNamespace(status_code=200, text=""))

    chunks.upload_object(str(source), _container(chunks), "data/a.txt", extra={})

    prepare, commit = chunks.session.sent
    assert type(prepare) is datachunks.PrepareUploadRequest
    assert prepare.to_dict() == {
        "task": "task-1",
        "kind": "dataset",
        "name": "data/a.txt",
        "size": 3,
        "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        "content_type": "text/plain",
    }
    assert type(commit) is datachunks.CommitRequest
    assert commit.to_dict() == {"id": "g1"}


def test_a_refused_datachunk_call_raises_a_storage_error(monkeypatch):
    session = RecordingSession(ok=False)
    monkeypatch.setattr(InterfaceBase, "_get_default_session", classmethod(lambda cls: session))

    with pytest.raises(StorageError, match="datachunks.delete failed"):
        _DatachunkDriver().delete_object(
            _DatachunkDriver._Object(_DatachunkDriver._Container("datachunk://dataset/task-1"), "a")
        )


# static routes: refused locally, the server has no routers service


def test_a_task_refuses_a_static_route_without_asking_the_server(monkeypatch):
    def no_server(cls):
        raise AssertionError("the server must not be asked about a static route")

    monkeypatch.setattr(InterfaceBase, "_get_default_session", classmethod(no_server))
    task = Task.__new__(Task)

    with pytest.raises(ValueError, match="static routes are not supported by this server; remove static_route"):
        task.request_external_endpoint(port=8080, static_route="my-route")


def test_a_router_refuses_a_static_route_before_it_starts_a_proxy():
    router = HttpRouter.__new__(HttpRouter)
    router._proxy = None

    with pytest.raises(ValueError, match="static routes are not supported by this server"):
        router.deploy(static_route="my-route")

    assert router._proxy is None


def test_the_routers_interface_is_gone():
    assert importlib.util.find_spec("cairnml.backend_interface.routers") is None


# the vault setting: the server has no vault endpoint, so nothing reads the setting any more


def test_the_vault_setting_is_gone_from_the_code_and_the_docs():
    assert not hasattr(defs, "ENV_DISABLE_VAULT_SUPPORT")
    assert "CAIRNML_DISABLE_VAULT_SUPPORT" not in (REPO_ROOT / "docs" / "environment.md").read_text()
    for path in (REPO_ROOT / "cairnml").rglob("*.py"):
        assert "CAIRNML_DISABLE_VAULT_SUPPORT" not in path.read_text(encoding="utf-8"), path

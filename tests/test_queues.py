import logging

import pytest

from cairnml import Task, queues
from cairnml.backend_api.session.callresult import CallResult
from cairnml.backend_api.session.response import ResponseMeta
from cairnml.backend_api.session.session import Session
from cairnml.backend_interface.base import InterfaceBase
from cairnml.debugging.log import LoggerRoot
from cairnml import task as task_module


class FakeApi:
    """A stand-in for the server. It answers the requests the queue helpers and enqueue send."""

    def __init__(self, queues=(), workers=(), queue_watched=None):
        self.queues = list(queues)
        self.workers = list(workers)
        self.queue_watched = queue_watched
        self.sent = []

    def send(self, req, async_enable=False):
        key = (req._service, req._action)
        self.sent.append(key)
        if key == ("queues", "get_all"):
            data = {"queues": self.queues}
        elif key == ("workers", "get_all"):
            data = {"workers": self.workers}
        elif key == ("tasks", "enqueue"):
            data = {"queued": 1, "updated": 1, "fields": {"execution.queue": "q-default"}}
            if self.queue_watched is not None:
                data["queue_watched"] = self.queue_watched
        else:
            raise AssertionError(f"unexpected request {key}")
        return CallResult(meta=ResponseMeta.from_raw_data(status_code=200, text=""), response_data=data)


QUEUES = [{"id": "q-default", "name": "default"}, {"id": "q-gpu", "name": "gpu"}]
WORKERS = [
    {"id": "agent-1", "queues": [{"id": "q-default", "name": "default"}]},
    {"id": "agent-2", "queues": [{"id": "q-gpu", "name": "gpu"}, {"id": "q-default", "name": "default"}]},
    {"id": "agent-3", "queues": None},
]


def test_list_queues_returns_name_and_id():
    api = FakeApi(queues=QUEUES)
    assert queues.list_queues(session=api) == [
        queues.Queue(id="q-default", name="default"),
        queues.Queue(id="q-gpu", name="gpu"),
    ]


def test_list_queues_is_empty_when_server_has_none():
    assert queues.list_queues(session=FakeApi()) == []


def test_list_workers_names_the_queues_of_each_worker():
    api = FakeApi(workers=WORKERS)
    assert queues.list_workers(session=api) == [
        queues.Worker(id="agent-1", queues=("default",)),
        queues.Worker(id="agent-2", queues=("gpu", "default")),
        queues.Worker(id="agent-3", queues=()),
    ]


def test_workers_for_queue_returns_only_workers_listening_to_it():
    api = FakeApi(workers=WORKERS)
    assert [w.id for w in queues.workers_for_queue("gpu", session=api)] == ["agent-2"]
    assert [w.id for w in queues.workers_for_queue("default", session=api)] == ["agent-1", "agent-2"]


def test_workers_for_unserved_queue_is_empty():
    # a mistyped queue name has no worker, so the list is empty instead of an error
    assert queues.workers_for_queue("gpu-1", session=FakeApi(workers=WORKERS)) == []


@pytest.fixture
def fake_server(monkeypatch):
    """Route Task.enqueue to a FakeApi that reports API 2.23, and capture the log warnings."""
    warnings = []

    class Recorder(logging.Handler):
        def emit(self, record):
            if record.levelno == logging.WARNING:
                warnings.append(record.getMessage())

    handler = Recorder()
    base_logger = LoggerRoot.get_base_logger()
    base_logger.addHandler(handler)

    previous = InterfaceBase._default_session

    def install(api, api_version="2.23"):
        monkeypatch.setattr(
            Session,
            "check_min_api_version",
            classmethod(lambda cls, v, raise_error=False: _version(v) <= _version(api_version)),
        )
        monkeypatch.setattr(Session, "api_version", api_version)
        monkeypatch.setattr(Session, "max_api_version", api_version)
        monkeypatch.setattr(task_module, "get_queue_id", lambda session, name: "q-" + name if name else None)
        InterfaceBase._set_default_session(api)
        return warnings

    yield install

    base_logger.removeHandler(handler)
    InterfaceBase._set_default_session(previous)


def _version(v):
    return tuple(int(x) for x in str(v).split("."))


def test_enqueue_asks_the_server_to_verify_the_queue(fake_server):
    api = FakeApi(queue_watched=True)
    fake_server(api)
    sent = []
    original_send = api.send

    def recording_send(req, async_enable=False):
        if (req._service, req._action) == ("tasks", "enqueue"):
            sent.append(req.to_dict())
        return original_send(req, async_enable=async_enable)

    api.send = recording_send
    Task.enqueue("task-1", queue_name="default")
    assert sent and sent[0]["verify_watched_queue"] is True


def test_enqueue_warns_when_no_worker_listens_to_the_queue(fake_server):
    warnings = fake_server(FakeApi(queue_watched=False))
    Task.enqueue("task-1", queue_name="gpu-1")
    assert len(warnings) == 1
    assert 'queue "gpu-1"' in warnings[0]
    assert "No worker is listening" in warnings[0]


def test_enqueue_does_not_warn_when_a_worker_listens(fake_server):
    warnings = fake_server(FakeApi(queue_watched=True))
    Task.enqueue("task-1", queue_name="default")
    assert warnings == []


def test_enqueue_does_not_warn_on_a_server_without_the_check(fake_server):
    # servers before API 2.22 do not answer queue_watched, so there is nothing to report
    warnings = fake_server(FakeApi(queue_watched=None), api_version="2.20")
    Task.enqueue("task-1", queue_name="default")
    assert warnings == []

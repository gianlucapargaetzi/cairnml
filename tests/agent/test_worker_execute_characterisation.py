"""What Worker.execute does today, pinned before it is split (issue #46, slice 1).

The method is called unbound with a stub worker: the session, the monitors and the run
of the task are recorded, not performed. The host-mode path runs subprocesses and is
covered by the characterisation of the command it builds in a later slice.
"""

from types import SimpleNamespace

import pytest

from cairnml_agent.commands import worker as worker_module
from cairnml_agent.commands.worker import Worker
from cairnml_agent.helper.base import CommandFailedError


class Reply:
    def __init__(self, ok=True, updated=1):
        self.ok = ok
        self._updated = updated

    def json(self):
        return {"data": {"updated": self._updated}}


class Tasks:
    def __init__(self, task, fail_lookup=None):
        self.task = task
        self.fail_lookup = fail_lookup
        self.started_calls = []

    def get_by_id(self, task_id):
        if self.fail_lookup:
            raise self.fail_lookup
        return self.task

    def started(self, **kwargs):
        self.started_calls.append(kwargs)


class Session:
    def __init__(self, task=None, fail_lookup=None, reply=None):
        self.api_client = SimpleNamespace(tasks=Tasks(task, fail_lookup))
        self.reply = reply or Reply(updated=0)
        self.requests = []
        self.config = {"api.host": "https://api.example"}
        self.config_file = "/tmp/unused.conf"
        self.debug_mode = False
        self.trace = False

    def send_request(self, **kwargs):
        self.requests.append(kwargs)
        return self.reply

    def print_configuration(self):
        pass


class Recorder:
    # the methods execute delegates to: the real ones, so the tests exercise the moved code
    _prepare_compiled_bootstrap = Worker._prepare_compiled_bootstrap
    _clone_or_dequeue = Worker._clone_or_dequeue
    _run_in_worker = Worker._run_in_worker
    _build_command = Worker._build_command
    _prepare_environment = Worker._prepare_environment
    _run_command = Worker._run_command
    _finish_run = Worker._finish_run
    _run_custom_build = Worker._run_custom_build
    _select_requirements = Worker._select_requirements
    _take_freeze = Worker._take_freeze

    """A stub worker: records the calls execute makes on it."""

    def __init__(self, session, status=7):
        self._session = session
        self._standalone_mode = None
        self._compiled = False
        self._bootstrap = False
        self.temp_config_path = None
        self.calls = []
        self._status = status

    def set_docker_variables(self, docker, clean_api_credentials=False):
        self.calls.append(("set_docker_variables", docker))

    def report_monitor(self, report):
        self.calls.append(("report_monitor",))

    def run_one_task(self, queue, task_id, worker_args, docker=None, task_session=None):
        self.calls.append(("run_one_task", queue, task_id, docker))
        return self._status

    def stop_monitor(self):
        self.calls.append(("stop_monitor",))

    def _unregister(self):
        self.calls.append(("_unregister",))


def task(task_id="task-1"):
    return SimpleNamespace(id=task_id, name="a task", script=SimpleNamespace(binary="python3", requirements=None))


@pytest.fixture
def no_bootstrap(monkeypatch):
    monkeypatch.setattr(worker_module.bootstrap, "check_bootstrap_package_install", lambda config: False)


def test_a_missing_task_id_is_refused_before_anything_is_called():
    stub = Recorder(Session(task()))
    with pytest.raises(CommandFailedError, match="must have valid task id"):
        Worker.execute(stub, task_id="", log_level="INFO")
    assert stub.calls == []


def test_a_task_that_cannot_be_found_is_a_value_error_with_the_host_named():
    stub = Recorder(Session(fail_lookup=ConnectionError("down")))
    with pytest.raises(ValueError, match="(?s)Could not find task id=task-1 .*api.example.*down"):
        Worker.execute(stub, task_id="task-1", log_level="INFO")


def test_a_lookup_that_returns_no_task_fields_is_a_value_error():
    class NoAttribute:
        def get_by_id(self, task_id):
            raise AttributeError("no id")

    session = Session()
    session.api_client = SimpleNamespace(tasks=NoAttribute())
    with pytest.raises(ValueError, match="Could not find task id=task-1"):
        Worker.execute(Recorder(session), task_id="task-1", log_level="INFO")


def test_docker_mode_runs_the_task_in_the_worker_and_reports_the_status(monkeypatch, no_bootstrap):
    monkeypatch.delenv("CAIRNML_AGENT_PROPAGATE_EXITCODE", raising=False)
    stub = Recorder(Session(task()), status=7)
    status = Worker.execute(stub, task_id="task-1", log_level="INFO", docker="ubuntu:22.04")
    # without the propagate switch the exit code is 0, whatever the run returned
    assert status == 0
    assert stub.calls == [
        ("set_docker_variables", "ubuntu:22.04"),
        ("report_monitor",),
        ("run_one_task", "", "task-1", "ubuntu:22.04"),
        ("stop_monitor",),
        ("_unregister",),
    ]


def test_docker_mode_returns_the_run_status_when_the_exit_code_is_propagated(monkeypatch, no_bootstrap):
    monkeypatch.setattr(worker_module, "ENV_PROPAGATE_EXITCODE", SimpleNamespace(get=lambda: True))
    stub = Recorder(Session(task()), status=7)
    assert Worker.execute(stub, task_id="task-1", log_level="INFO", docker="ubuntu:22.04") == 7


def test_docker_mode_still_stops_the_monitor_when_the_run_raises(monkeypatch, no_bootstrap):
    stub = Recorder(Session(task()))

    def fail(*args, **kwargs):
        raise RuntimeError("container failed to start")

    stub.run_one_task = fail
    with pytest.raises(RuntimeError, match="container failed"):
        Worker.execute(stub, task_id="task-1", log_level="INFO", docker="ubuntu:22.04")
    assert ("stop_monitor",) in stub.calls
    assert ("_unregister",) in stub.calls


def test_clone_asks_the_server_for_a_copy_and_does_not_dequeue(monkeypatch, no_bootstrap):
    session = Session(task())
    session.api_client.tasks.get_by_id = lambda task_id: task(task_id)
    monkeypatch.setattr(
        worker_module.tasks_api, "CloneRequest", lambda **kwargs: SimpleNamespace(**kwargs), raising=False
    )
    session.send_api = lambda request: SimpleNamespace(id="task-2")
    stub = Recorder(session)
    Worker.execute(stub, task_id="task-1", log_level="INFO", docker="ubuntu:22.04", clone=True)
    assert session.requests == []  # no dequeue for a clone
    assert ("run_one_task", "", "task-2", "ubuntu:22.04") in stub.calls


def test_a_task_that_must_be_queued_and_is_not_fails(monkeypatch, no_bootstrap):
    session = Session(task(), reply=Reply(ok=True, updated=0))
    stub = Recorder(session)
    with pytest.raises(ValueError, match="is not queued"):
        Worker.execute(stub, task_id="task-1", log_level="INFO", require_queue=True)
    assert session.requests[0]["json"] == {"task": "task-1", "new_status": "in_progress"}

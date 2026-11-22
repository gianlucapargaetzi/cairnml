"""A job whose launch cannot be prepared ends as failed, with the reason in its status; the daemon keeps taking jobs.

Before this, an exception while the Docker command was built left the task in progress and escaped the daemon loop,
which then started polling again (issue #145)."""

import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnml_agent.commands import worker as worker_module
from cairnml_agent.commands.worker import Worker
from cairnml_agent.errors import CommandFailedError
from cairnml_agent.helper.process import ExitStatus

from .wire import WireSession, answer

TASK = {"id": "task-1", "name": "train", "status": "queued"}


def session_for(*queued_tasks):
    answers = {
        "tasks.get_all": answer({"tasks": [TASK]}),
        "tasks.edit": answer({"updated": 1}),
        "tasks.started": answer({}),
        "tasks.failed": answer({}),
        "tasks.stopped": answer({}),
        "tasks.completed": answer({}),
        "events.add_batch": answer({}),
    }
    return WireSession(answers)


def launch_stub(session, docker_image_func, docker_arguments=None):
    """The attributes of a daemon that runs a task in a Docker container, with the real launch-failure method."""
    events = RecordingEvents()
    stub = SimpleNamespace(
        _session=session,
        _task_logging_start_message="Running task '{}'",
        hostname_task_runtime_prop="hostname",
        _get_node_rank=lambda: 0,
        _get_job_token=lambda task_id: ("token-1", 3600),
        _get_docker_restart_value=lambda task_session, task_id: None,
        _resolve_id=lambda id, service, search_hidden: "queue-name",
        _docker_image="ubuntu:22.04",
        _docker_arguments=docker_arguments,
        _docker_default_cmd_override=False,
        _services_mode=False,
        _worker_tags=None,
        _docker_force_pull=False,
        worker_id="worker-1",
        docker_image_func=docker_image_func,
        log=lambda *args, **kwargs: None,
    )
    stub.get_service = lambda service_class: events
    stub.send_logs = types.MethodType(Worker.send_logs, stub)
    stub._fail_job_launch = types.MethodType(Worker._fail_job_launch, stub)
    stub.events = events
    return stub


class RecordingEvents:
    """The task log: what the agent sends to it, without an enrolled session behind it."""

    def __init__(self):
        self.lines = []

    def send_log_events(self, worker_id, task_id, lines, level="DEBUG", session=None):
        self.lines.extend(lines)


def test_a_launch_that_raises_ends_the_task_failed_with_the_reason():
    session = session_for(TASK)
    seen = {}

    def docker_image_func(env_task_id, **params):
        seen["job_token_dir"] = Path(params["job_token_dir"])
        raise CommandFailedError("no wheel")

    stub = launch_stub(session, docker_image_func)
    assert Worker.run_one_task(stub, "queue", "task-1", {}) == ExitStatus.failure

    [failed] = session.of("tasks.failed")
    assert failed.json["task"] == "task-1"
    assert "no wheel" in failed.json["status_message"]
    assert "The job could not be started" in failed.json["status_message"]
    assert session.of("tasks.completed") == [] and session.of("tasks.stopped") == []
    assert not seen["job_token_dir"].exists()
    assert any("no wheel" in line for line in stub.events.lines)


def test_a_template_error_while_the_docker_command_is_resolved_also_fails_the_task(monkeypatch):
    def unresolvable(self, full_docker_cmd):
        raise ValueError("unknown variable")

    monkeypatch.setattr(worker_module.DockerArgsTemplateResolver, "resolve_from_docker_cmd", unresolvable)
    session = session_for(TASK)
    stub = launch_stub(session, lambda env_task_id, **params: ["docker", "run", "x"], docker_arguments=["--gpus"])

    assert Worker.run_one_task(stub, "queue", "task-1", {}) == ExitStatus.failure

    [failed] = session.of("tasks.failed")
    assert "unknown variable" in failed.json["status_message"]


def test_a_job_without_a_token_ends_the_task_failed_and_is_not_left_in_progress():
    session = session_for(TASK)
    stub = launch_stub(session, lambda env_task_id, **params: ["docker", "run", "x"])
    stub._get_job_token = lambda task_id: (None, None)

    assert Worker.run_one_task(stub, "queue", "task-1", {}) == ExitStatus.failure

    [failed] = session.of("tasks.failed")
    assert "job token" in failed.json["status_message"]
    assert session.of("tasks.stopped") == []


def test_a_failure_to_report_the_failure_does_not_stop_the_daemon():
    session = session_for(TASK)
    session.answers["tasks.failed"] = answer(status=500, msg="server down")

    def docker_image_func(env_task_id, **params):
        raise CommandFailedError("no wheel")

    stub = launch_stub(session, docker_image_func)
    assert Worker.run_one_task(stub, "queue", "task-1", {}) == ExitStatus.failure
    assert len(session.of("tasks.failed")) == 1


def test_the_daemon_loop_takes_the_next_job_after_a_launch_that_failed(monkeypatch):
    """The loop pulls task-1, its launch raises, then task-2 is pulled and launched; the loop does not raise."""
    launched = []

    class StopLoop(BaseException):
        pass

    pulls = iter([{"entry": {"task": "task-1"}}, {"entry": {"task": "task-2"}}])

    def next_task(session, queue):
        try:
            return next(pulls)
        except StopIteration:
            raise StopLoop() from None

    def docker_image_func(env_task_id, **params):
        launched.append(env_task_id)
        raise CommandFailedError("no wheel")

    monkeypatch.setattr(worker_module, "get_next_task", next_task)
    session = session_for(TASK)
    stub = launch_stub(session, docker_image_func)
    stub.run_one_task = types.MethodType(Worker.run_one_task, stub)
    stub.docker_image_func = docker_image_func
    stub.monitor = object()
    stub.queues = ["queue"]
    stub._daemon_foreground = False
    stub._polling_interval = 0
    stub._redirected_stdout_file_no = None
    stub.report_monitor = lambda *args, **kwargs: None
    stub._get_queue_tags = lambda queue_ids: {"queue": {"name": "queue", "tags": []}}
    stub._queue_is_pulled_from = Worker._queue_is_pulled_from
    stub._unregister = lambda: None
    stub.reload_config = lambda: None

    with pytest.raises(StopLoop):
        Worker.run_tasks_loop(stub, ["queue"], SimpleNamespace(debug=False))
    assert launched == ["task-1", "task-2"]
    assert len(session.of("tasks.failed")) == 2

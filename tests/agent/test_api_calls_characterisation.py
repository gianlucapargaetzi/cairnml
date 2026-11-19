"""What the agent's API calls put on the wire, and what the agent does with the answer.

The tests say what each call site asks (service, action, fields) and how it handles the server's answer. They do not
depend on how the call is built, by hand or from a generated class: the agent's calls were moved from the first to the
second, and these tests held on both sides of that move. The API version a call goes out at is a separate rule, in
test_api_calls_version.py.
"""

import json
import types
from types import SimpleNamespace

import pytest

from cairnml_agent.commands import worker as worker_module
from cairnml_agent.commands.events import Events
from cairnml_agent.commands.resolver import resolve_default_container
from cairnml_agent.commands.worker import (
    TaskNotFoundError,
    TaskStopSignal,
    Worker,
    get_next_task,
    get_task,
    get_task_container,
    get_task_fields,
    set_task_container,
)
from cairnml_agent.errors import APIError
from cairnml_agent.helper.custom_template import TemplateResolver
from cairnml_agent.helper.docker_args import DockerArgsTemplateResolver
from cairnml_agent.helper.job_token import request_job_token
from cairnml_agent.helper.repo import request_git_token
from cairnml_agent.helper.resource_monitor import ResourceMonitor
from cairnml_agent.helper.task_runtime import TaskRuntime

from .wire import WireSession, answer, not_json

TASK = {"id": "task-1", "name": "train", "status": "queued"}


@pytest.fixture(autouse=True)
def advanced_feature_set(monkeypatch):
    """Container match rules are for the advanced feature set, which the session reads from the server's token."""
    monkeypatch.setattr(WireSession, "feature_set", "advanced")


def bind(target, cls, *names):
    """Give a stub object the real methods of a class, so a test runs the agent's code on a plain stand-in."""
    for name in names:
        setattr(target, name, types.MethodType(getattr(cls, name), target))
    return target


# ---- tasks: one task, its fields, its container -------------------------------------------------------------------


def test_a_task_is_read_with_hidden_ones_searched_and_the_fields_asked_for():
    session = WireSession({"tasks.get_all": answer({"tasks": [TASK]})})
    task = get_task(session, "task-1", only_fields=["status"])
    assert task.id == "task-1"
    [call] = session.of("tasks.get_all")
    assert call.json == {
        "id": ["task-1"],
        "search_hidden": True,
        "only_fields": ["status"],
        "include_subprojects": False,
    }


def test_a_missing_task_raises_not_found():
    session = WireSession({"tasks.get_all": answer({"tasks": []})})
    with pytest.raises(TaskNotFoundError):
        get_task(session, "task-1")


def test_a_refused_task_read_raises_the_servers_error():
    session = WireSession({"tasks.get_all": answer(status=404, msg="no such task", subcode=3)})
    with pytest.raises(APIError) as caught:
        get_task(session, "task-1")
    assert caught.value.code == 404


def test_the_next_task_is_the_queues_entry():
    session = WireSession({"queues.get_next_task": answer({"entry": {"task": "task-9", "queue": "q"}})})
    assert get_next_task(session, queue="q") == {"entry": {"task": "task-9", "queue": "q"}}
    assert [call.json for call in session.of("queues.get_next_task")] == [{"queue": "q", "get_task_info": False}]


def test_an_empty_queue_answers_an_entry_less_result():
    session = WireSession({"queues.get_next_task": answer({})})
    assert get_next_task(session, queue="q") == {}


def test_a_refused_next_task_raises_the_servers_error():
    session = WireSession({"queues.get_next_task": answer(status=400, msg="bad queue", subcode=1)})
    with pytest.raises(APIError):
        get_next_task(session, queue="q")


def test_task_fields_are_read_along_their_dotted_paths():
    task = {"container": {"image": "ubuntu"}, "runtime": {"a": "1"}}
    session = WireSession({"tasks.get_all": answer({"tasks": [task]})})
    assert get_task_fields(session, "task-1", ["container.image", "runtime.a"]) == {
        "container.image": "ubuntu",
        "runtime.a": "1",
    }
    [call] = session.of("tasks.get_all")
    assert call.json == {
        "id": ["task-1"],
        "only_fields": ["container.image", "runtime.a"],
        "search_hidden": True,
        "include_subprojects": False,
    }


def test_a_container_is_split_into_its_arguments():
    task = {"container": {"image": " ubuntu ", "arguments": "-v /a:/b --gpus all", "setup_shell_script": "ls"}}
    session = WireSession({"tasks.get_all": answer({"tasks": [task]})}, config={})
    container = get_task_container(session, "task-1", allow_force_container_rules=False)
    assert container == {"image": "ubuntu", "arguments": ["-v", "/a:/b", "--gpus", "all"], "setup_shell_script": "ls"}
    [call] = session.of("tasks.get_all")
    assert call.json == {
        "id": ["task-1"],
        "only_fields": ["container"],
        "search_hidden": True,
        "include_subprojects": False,
    }


def test_a_refused_container_read_is_no_container():
    session = WireSession({"tasks.get_all": answer(status=404, msg="no such task")}, config={})
    assert get_task_container(session, "task-1", allow_force_container_rules=False) == {}


def test_the_container_of_a_task_is_set_forcibly_and_the_answer_says_whether_it_worked():
    session = WireSession({"tasks.edit": [answer({"updated": 1}), answer(status=400, msg="no")]})
    assert set_task_container(session, "task-1", "ubuntu", ["-v", "/a:/b"], "ls") is True
    assert set_task_container(session, "task-1", "ubuntu") is False
    first, second = session.of("tasks.edit")
    assert first.json == {
        "task": "task-1",
        "container": {"image": "ubuntu", "arguments": "-v /a:/b", "setup_shell_script": "ls"},
        "force": True,
    }
    assert second.json["container"] == {"image": "ubuntu", "arguments": "", "setup_shell_script": ""}


RULES = [
    {"match": {"project": "^vision$"}, "image": "vision:1", "arguments": "--shm-size 2g", "force_container_rules": 1}
]


def rule_session(**answers):
    session = WireSession(
        {
            "tasks.get_all": answer(
                {"tasks": [{"project": "p-1", "container": {}, "script": {}, "tags": [], "user": "u"}]}
            ),
            "projects.get_all": answer({"projects": [{"id": "p-1", "name": "vision"}]}),
            **answers,
        },
        config={"agent": {"default_docker": {"match_rules": RULES}}},
    )
    return session


def test_a_matching_rule_gives_the_container_it_names():
    session = rule_session()
    container, entry = resolve_default_container(session, "task-1", {})
    assert entry == RULES[0]
    assert container["image"] == "vision:1" and container["arguments"] == "--shm-size 2g"
    [tasks_call] = session.of("tasks.get_all")
    assert tasks_call.json == {
        "include_subprojects": False,
        "id": ["task-1"],
        "only_fields": [
            "script.requirements",
            "script.binary",
            "script.repository",
            "script.branch",
            "project",
            "container",
            "tags",
            "user",
        ],
        "search_hidden": True,
    }
    [projects_call] = session.of("projects.get_all")
    assert projects_call.json == {
        "id": ["p-1"],
        "only_fields": ["name"],
        "search_hidden": False,
        "shallow_search": False,
    }


def test_without_match_rules_the_server_is_not_asked():
    session = WireSession({}, config={})
    assert resolve_default_container(session, "task-1", {"image": "x"}) == ({"image": "x"}, None)
    assert session.calls == []


def test_a_forced_rule_replaces_the_container_and_updates_the_task_back(monkeypatch):
    entry = {**RULES[0], "update_back_task": True}
    session = WireSession(
        {
            "tasks.get_all": answer({"tasks": [{"container": {"image": "own:1", "arguments": "-x"}}]}),
            "tasks.edit": answer({"updated": 1}),
        },
    )

    def forced_rule(session, task_id, container_config, ignore_match_rules=False):
        # the resolver's answer is the container of the rule, with its flags (a new dict: the task is updated back
        # only when the container differs from the one the task had)
        return {**container_config, "image": "vision:1", "force_container_rules": True, "update_back_task": True}, entry

    monkeypatch.setattr(worker_module, "resolve_default_container", forced_rule)
    container = get_task_container(session, "task-1")
    assert container["image"] == "vision:1" and container["arguments"] == ["-x"]
    [edit] = session.of("tasks.edit")
    assert edit.json == {
        "task": "task-1",
        "force": True,
        "container": {"image": "vision:1", "arguments": "-x", "setup_shell_script": ""},
    }


# ---- the abort callback of a task ---------------------------------------------------------------------------------


def stop_signal(session):
    command = SimpleNamespace(worker_id="worker-1", log=lambda *a, **k: None, send_logs=lambda *a, **k: None)
    return TaskStopSignal(command, session, None, "task-1")


def test_registering_a_callback_stores_its_timeouts_in_the_task_runtime(monkeypatch):
    monkeypatch.setenv("CAIRNML_AGENT_ABORT_CALLBACK_CMD_REGISTERED", "")  # the registration sets it; restored after
    monkeypatch.setenv("CAIRNML_AGENT_ABORT_CALLBACK_CMD", "echo bye")
    session = WireSession({"tasks.get_all": answer({"tasks": [{"runtime": {"keep": "1"}}]}), "tasks.edit": answer({})})
    stop_signal(session).register_bash_callback()
    [read] = session.of("tasks.get_all")
    assert read.json == {
        "id": ["task-1"],
        "only_fields": ["runtime"],
        "search_hidden": False,
        "include_subprojects": False,
    }
    [edit] = session.of("tasks.edit")
    assert edit.json["task"] == "task-1" and edit.json["force"] is True
    assert edit.json["runtime"]["keep"] == "1"
    assert edit.json["runtime"]["_abort_callback_timeout"] == "180"
    assert edit.json["runtime"]["_abort_poll_freq"] == "10"


def test_the_abort_callback_state_is_read_from_the_task_runtime():
    runtime = {"_abort_callback_timeout": "30", "_abort_poll_freq": "10", "_abort_callback_completed": "1"}
    session = WireSession({"tasks.get_all": answer({"tasks": [{"runtime": runtime}]})})
    assert stop_signal(session)._get_abort_callback_stat() == ("30", "10", "1")
    [call] = session.of("tasks.get_all")
    assert call.json == {
        "id": ["task-1"],
        "search_hidden": False,
        "include_subprojects": False,
        "only_fields": [
            "status",
            "status_message",
            "runtime._abort_callback_timeout",
            "runtime._abort_poll_freq",
            "runtime._abort_callback_completed",
        ],
    }


def test_a_failed_abort_callback_read_is_no_state():
    session = WireSession({"tasks.get_all": answer(status=404, msg="no such task")})
    assert stop_signal(session)._get_abort_callback_stat() == (None, None, None)


def test_a_running_callback_completes_when_the_runtime_says_so():
    session = WireSession({"tasks.get_all": answer({"tasks": [{"runtime": {"_abort_callback_completed": "1"}}]})})
    signal = stop_signal(session)
    signal._support_callback = True
    signal._active_callback_timestamp = __import__("time").time()
    signal._active_callback_timeout = 600
    assert signal._wait_for_abort_callback() is True
    [call] = session.of("tasks.get_all")
    assert call.json["only_fields"] == ["status", "status_message", "runtime._abort_callback_completed"]


# ---- the worker: restart option, hostname, dequeue, hyper parameters, workers service -----------------------------


def test_a_restart_selector_is_matched_against_the_tasks_field(monkeypatch):
    monkeypatch.setenv("CAIRNML_AGENT_SERVICES_DOCKER_RESTART", "always;runtime.mode=fast")
    session = WireSession({"tasks.get_all": answer({"tasks": [{"runtime": {"mode": "fast"}}]})})
    stub = SimpleNamespace(_session=session, log=SimpleNamespace(error=lambda *a, **k: None))
    stub._get_path = Worker._get_path
    assert Worker._get_docker_restart_value(stub, session, "task-1") == "always"
    [call] = session.of("tasks.get_all")
    assert call.json == {
        "id": ["task-1"],
        "only_fields": ["runtime.mode"],
        "search_hidden": True,
        "include_subprojects": False,
    }


def test_a_restart_selector_that_does_not_match_gives_no_restart(monkeypatch):
    monkeypatch.setenv("CAIRNML_AGENT_SERVICES_DOCKER_RESTART", "always;runtime.mode=fast")
    session = WireSession({"tasks.get_all": answer({"tasks": [{"runtime": {"mode": "slow"}}]})})
    stub = SimpleNamespace(_session=session, log=SimpleNamespace(error=lambda *a, **k: None))
    stub._get_path = Worker._get_path
    assert Worker._get_docker_restart_value(stub, session, "task-1") is None


def test_a_refused_restart_selector_read_is_logged_and_gives_no_restart(monkeypatch):
    monkeypatch.setenv("CAIRNML_AGENT_SERVICES_DOCKER_RESTART", "always;runtime.mode=fast")
    session = WireSession({"tasks.get_all": answer(status=404, msg="no such task")})
    errors = []
    stub = SimpleNamespace(_session=session, log=SimpleNamespace(error=errors.append))
    stub._get_path = Worker._get_path
    assert Worker._get_docker_restart_value(stub, session, "task-1") is None
    assert errors and "no such task" in errors[0]


def test_hyper_parameters_with_the_environment_section_become_the_task_environment():
    hyper = {"params": [{"hyperparams": [{"section": "Environment", "name": "A", "value": 1}]}]}
    session = WireSession({"tasks.get_hyper_params": answer(hyper)}, config={"agent": {"enable_task_env": True}})
    stub = SimpleNamespace(_session=session)
    assert Worker._get_task_os_env(stub, session.config, SimpleNamespace(id="task-1")) == {"A": "1"}
    [call] = session.of("tasks.get_hyper_params")
    assert call.json == {"tasks": ["task-1"]}


def test_a_failed_hyper_parameter_read_is_no_environment():
    session = WireSession(
        {"tasks.get_hyper_params": answer(status=404, msg="no")}, config={"agent": {"enable_task_env": True}}
    )
    stub = SimpleNamespace(_session=session)
    assert Worker._get_task_os_env(stub, session.config, SimpleNamespace(id="task-1")) is None


def test_a_task_is_dequeued_to_in_progress_and_started_when_it_was_queued():
    session = WireSession({"tasks.dequeue": answer({"updated": 1}), "tasks.started": answer({})})
    stub = SimpleNamespace(_session=session)
    task = SimpleNamespace(id="task-1")
    assert Worker._clone_or_dequeue(stub, "task-1", task, False, True) is task
    [call] = session.of("tasks.dequeue")
    assert call.json == {"task": "task-1", "new_status": "in_progress", "remove_from_all_queues": False}
    [started] = session.of("tasks.started")
    assert started.json["task"] == "task-1" and started.json["force"] is True


def test_a_task_that_is_not_queued_stops_a_run_that_requires_the_queue():
    session = WireSession({"tasks.dequeue": answer({"updated": 0})})
    stub = SimpleNamespace(_session=session)
    with pytest.raises(ValueError, match="not queued"):
        Worker._clone_or_dequeue(stub, "task-1", SimpleNamespace(id="task-1"), False, True)


def test_the_hostname_is_stored_in_the_task_runtime_before_the_task_starts(monkeypatch):
    session = WireSession(
        {
            "tasks.get_all": answer({"tasks": [{"runtime": {"keep": "1"}}]}),
            "tasks.edit": answer({"updated": 1}),
            "tasks.started": answer(status=400, msg="stop here"),
        }
    )
    stub = SimpleNamespace(
        _task_logging_start_message="Running task '{}'",
        _session=session,
        hostname_task_runtime_prop="hostname",
        _get_node_rank=lambda: 0,
    )
    assert Worker.run_one_task(stub, "queue", "task-1", None) is None
    [read] = session.of("tasks.get_all")
    assert read.json == {
        "id": ["task-1"],
        "only_fields": ["runtime"],
        "search_hidden": True,
        "include_subprojects": False,
    }
    [edit] = session.of("tasks.edit")
    assert edit.json["task"] == "task-1" and edit.json["force"] is True
    assert edit.json["runtime"]["keep"] == "1" and edit.json["runtime"]["hostname"]


def worker_stub(session):
    return SimpleNamespace(_session=session, service="workers", worker_id="worker-1", log=lambda *a, **k: None)


def test_the_worker_registers_with_its_queues():
    session = WireSession({"workers.register": answer({})})
    stub = worker_stub(session)
    Worker._register(stub, queues=["q-1"])
    assert stub._worker_registered is True
    [call] = session.of("workers.register")
    assert call.json == {"worker": "worker-1", "queues": ["q-1"], "timeout": 3600}


def test_a_refused_registration_is_logged_and_the_worker_is_not_registered():
    session = WireSession({"workers.register": answer(status=400, msg="refused", subcode=1)})
    stub = worker_stub(session)
    messages = []
    stub.log = messages.append
    Worker._register(stub, queues=["q-1"])
    assert not getattr(stub, "_worker_registered", False)
    assert messages and "failed registering" in messages[0]


def test_the_worker_unregisters_from_its_queues():
    session = WireSession({"workers.unregister": answer({})})
    stub = worker_stub(session)
    stub._worker_registered = True
    Worker._unregister(stub, queues=["q-1"])
    assert stub._worker_registered is False
    [call] = session.of("workers.unregister")
    assert call.json == {"worker": "worker-1"}


def test_the_workers_list_asks_for_every_worker_ever_seen(capsys):
    session = WireSession({"workers.get_all": answer({"workers": []})})
    stub = worker_stub(session)
    Worker.list(stub)
    [call] = session.of("workers.get_all")
    assert call.json == {"last_seen": 0}


# ---- the job's token and the repository's token -------------------------------------------------------------------


def test_the_job_token_is_asked_for_the_task():
    session = WireSession({"devices.job_token": answer({"access_token": "job-1", "expires_in": 3600})})
    assert request_job_token(session, "task-1") == ("job-1", 3600)
    [call] = session.of("devices.job_token")
    assert call.json == {"task": "task-1"}


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_refusal_of_the_job_token_is_none(status):
    session = WireSession({"devices.job_token": answer(status=status, msg="refused")})
    assert request_job_token(session, "task-1") is None


@pytest.mark.parametrize("reply", [answer(status=503, msg="restarting"), not_json(502)])
def test_any_other_failure_of_the_job_token_is_raised(reply):
    session = WireSession({"devices.job_token": reply})
    with pytest.raises(RuntimeError, match="failed"):
        request_job_token(session, "task-1")


def test_the_git_token_of_the_task_is_asked_for_and_the_answer_is_handed_on():
    session = WireSession({"devices.git_token": answer({"token": "tok", "username": "bot"})})
    reply = request_git_token(session, "task-1")
    [call] = session.of("devices.git_token")
    assert call.json == {"task": "task-1"}
    from cairnml_agent.helper.repo import git_credential_from_answer

    assert git_credential_from_answer(reply) == ("bot", "tok")


# ---- task runtime, user info, custom templates --------------------------------------------------------------------


def test_the_task_runtime_is_read_and_merged_when_it_is_updated():
    session = WireSession(
        {
            "tasks.get_by_id": answer({"task": {"runtime": {"a": "1"}}}),
            "tasks.edit": answer({"updated": 1}),
        }
    )
    runtime = TaskRuntime(session)
    assert runtime.get_task_runtime("task-1") == {"a": "1"}
    assert runtime.update_task_runtime("task-1", {"b": "2"}) is True
    [get] = [call for call in session.of("tasks.get_by_id")][:1]
    assert get.json == {"task": "task-1"}
    [edit] = session.of("tasks.edit")
    assert edit.json == {"task": "task-1", "force": True, "runtime": {"a": "1", "b": "2"}}


def test_a_failed_runtime_read_is_none_and_a_failed_update_is_false(capsys):
    session = WireSession({"tasks.get_by_id": answer(status=404, msg="no"), "tasks.edit": answer(status=400, msg="no")})
    runtime = TaskRuntime(session)
    assert runtime.get_task_runtime("task-1") is None
    assert runtime.update_task_runtime("task-1", {"b": "2"}) is False


def template_resolver(session):
    return TemplateResolver(session, "task-1", "queue-1", "queue", "worker-1")


def test_the_template_loads_the_current_user():
    session = WireSession({"users.get_current_user": answer({"user": {"id": "u-1", "name": "ann"}})})
    assert template_resolver(session)._load_user_info() == {"id": "u-1", "name": "ann"}
    assert [call.json for call in session.of("users.get_current_user")] in ([{}], [None])


def test_the_template_loads_the_task_and_its_project():
    session = WireSession(
        {
            "tasks.get_all": answer({"tasks": [{"id": "task-1", "project": "p-1"}]}),
            "projects.get_all": answer({"projects": [{"id": "p-1", "name": "vision"}]}),
        }
    )
    resolver = template_resolver(session)
    assert resolver._load_task_info() == {"id": "task-1", "project": "p-1"}
    assert resolver._load_project_info() == {"name": "vision", "id": "p-1"}
    assert session.of("tasks.get_all")[0].json == {
        "id": ["task-1"],
        "search_hidden": True,
        "include_subprojects": False,
    }
    assert session.of("projects.get_all")[0].json == {"id": ["p-1"], "search_hidden": True, "shallow_search": False}


def test_docker_arguments_are_resolved_against_the_task(monkeypatch):
    from cairnml_agent.helper.custom_template import CustomTemplate

    seen = []
    monkeypatch.setattr(
        CustomTemplate, "default_custom_substitute", lambda self, **kwargs: seen.append(self.template) or "x"
    )
    monkeypatch.setattr(CustomTemplate, "get_queue_name", lambda *args, **kwargs: "q")
    session = WireSession({}, config={})
    resolver = DockerArgsTemplateResolver(session, "t1", "q1", "q", "w1", task_info={})
    resolver.resolve_from_docker_cmd(["-e", "${CAIRNML_x}", "image"])
    assert seen == ["${CAIRNML_x}"]


# ---- reports and events -------------------------------------------------------------------------------------------


def test_the_resource_report_goes_to_the_workers_service():
    session = WireSession({"workers.status_report": answer({})})
    monitor = ResourceMonitor.__new__(ResourceMonitor)
    monitor.session = session
    monitor._worker_id = "worker-1"
    monitor._worker_tags = ["t"]
    monitor._run_mode = "venv"
    monitor.get_report = lambda: SimpleNamespace(to_dict=lambda: {"task": "task-1"})
    assert monitor.send_report(stats={"memory_used": 1.5, "network_tx_mbs": 2.0}) is True
    [call] = session.of("workers.status_report")
    assert call.json["worker"] == "worker-1" and call.json["task"] == "task-1" and call.json["run_mode"] == "venv"
    assert call.json["tags"] == ["t"] and call.json["machine_stats"] == {"memory_used": 1.5}


def test_a_refused_resource_report_is_not_sent():
    session = WireSession({"workers.status_report": answer(status=400, msg="no")})
    monitor = ResourceMonitor.__new__(ResourceMonitor)
    monitor.session = session
    monitor._worker_id = "worker-1"
    monitor._worker_tags = []
    monitor._run_mode = "venv"
    monitor.get_report = lambda: SimpleNamespace(to_dict=lambda: {})
    assert monitor.send_report(stats=None) is False


def events_stub(session):
    return SimpleNamespace(_session=session, service="events", max_packet_size=Events.max_packet_size)


def test_events_are_sent_as_json_lines_and_the_added_count_is_returned():
    session = WireSession({"events.add_batch": answer({"added": 2, "errors": 0})})
    events = [{"type": "log", "task": "task-1", "msg": "a"}, {"type": "log", "task": "task-1", "msg": "b"}]
    assert Events.send_events(events_stub(session), events) == 2
    [call] = session.of("events.add_batch")
    assert {k.lower(): v for k, v in call.headers.items()}["content-type"] == "application/json-lines"
    assert [json.loads(line) for line in call.data.split("\n")] == events


def test_events_the_server_did_not_register_are_reported(capsys):
    session = WireSession({"events.add_batch": answer({"added": 1, "errors": 1})})
    events = [{"msg": "a"}, {"msg": "b"}]
    assert Events.send_events(events_stub(session), events) == 1
    assert "only 1 of 2 registered" in capsys.readouterr().out


def test_a_refused_event_batch_raises_the_servers_error():
    session = WireSession({"events.add_batch": answer(status=400, msg="bad")})
    with pytest.raises(APIError):
        Events.send_events(events_stub(session), [{"msg": "a"}])


# ---- the Kubernetes glue and its pending-pods monitor -------------------------------------------------------------

K8S_TASK = {"id": "task-1", "name": "train"}


def k8s_glue(session, **attributes):
    from cairnml_agent.glue.k8s import K8sIntegration

    glue = K8sIntegration.__new__(K8sIntegration)
    glue._session = session
    glue.log = SimpleNamespace(
        error=lambda *a, **k: None, warning=lambda *a, **k: None, debug=lambda *a, **k: None, info=lambda *a, **k: None
    )
    glue.worker_id = "worker-1"
    glue.kind = "pod"
    glue.send_logs = lambda *a, **k: None
    for name, value in attributes.items():
        setattr(glue, name, value)
    return glue


def test_a_task_is_pushed_into_the_pending_queue_and_a_stale_queue_is_made_again_once():
    session = WireSession(
        {
            "tasks.get_all": answer({"tasks": [K8S_TASK]}),
            "queues.remove_task": answer({}),
            "tasks.enqueue": [answer(status=400, msg="invalid queue", subcode=701), answer(status=400, msg="nope")],
        }
    )
    ensured = []
    glue = k8s_glue(
        session,
        _is_same_tenant=lambda task_session: True,
        _server_supports_same_state_transition=True,
        k8s_pending_queue_id="pending-q",
        k8s_pending_queue_name="pending",
        _update_execution_queue_when_enqueueing_to_pending_queue=False,
        _ensure_pending_queue_exists=lambda: ensured.append(True),
    )
    assert glue.run_one_task("queue-1", "task-1") is None
    [read] = session.of("tasks.get_all")
    assert read.json == {"id": ["task-1"], "search_hidden": False, "include_subprojects": False}
    first, second = session.of("tasks.enqueue")
    assert (
        first.json
        == second.json
        == {
            "task": "task-1",
            "queue": "pending-q",
            "status_reason": "k8s pending scheduler",
            "update_execution_queue": False,
            "verify_watched_queue": False,
        }
    )
    assert ensured == [True]


def test_user_properties_are_set_as_hyper_parameters_of_the_properties_section():
    session = WireSession({"tasks.edit_hyper_params": answer({"updated": 1})})
    glue = k8s_glue(session, _edit_hyperparams_support=None)
    glue._set_task_user_properties("task-1", None, **{"k8s-queue": "q", "n": 3})
    [call] = session.of("tasks.edit_hyper_params")
    assert call.json == {
        "task": "task-1",
        "hyperparams": [
            {"section": "properties", "name": "k8s-queue", "value": "q"},
            {"section": "properties", "name": "n", "value": "3"},
        ],
    }
    assert glue._edit_hyperparams_support is None


def test_a_server_that_does_not_know_the_hyper_parameter_call_is_asked_no_more():
    session = WireSession({"tasks.edit_hyper_params": answer(status=404, msg="unknown")})
    glue = k8s_glue(session, _edit_hyperparams_support=None)
    glue._set_task_user_properties("task-1", None, a="1")
    assert glue._edit_hyperparams_support == session.api_version
    glue._set_task_user_properties("task-1", None, a="1")
    assert len(session.of("tasks.edit_hyper_params")) == 1


def test_the_runtime_of_a_task_gets_the_glues_values_and_keeps_the_old_ones():
    session = WireSession(
        {
            "tasks.get_by_id": answer({"task": {"runtime": {"old": "1"}}}),
            "tasks.edit": answer({"updated": 1}),
        }
    )
    glue = k8s_glue(session, _user_props_cb=None, _runtime_cb=lambda: {"new": "2"})
    glue._set_task_user_properties = lambda *a, **k: None
    glue.set_task_info("task-1", session, K8S_TASK, "queue", False, 0, 1)
    [read] = session.of("tasks.get_by_id")
    assert read.json == {"task": "task-1"}
    [edit] = session.of("tasks.edit")
    assert edit.json == {"task": "task-1", "force": True, "runtime": {"old": "1", "new": "2"}}


def test_a_task_of_a_deleted_pod_that_is_stale_is_failed_and_one_that_is_queued_is_dequeued_first():
    stale = {"id": "task-1", "status": "queued", "status_reason": "other", "last_change": None}
    session = WireSession(
        {
            "tasks.get_all": answer({"tasks": [stale]}),
            "tasks.dequeue": answer({"updated": 1}),
            "tasks.failed": answer({"updated": 1}),
        }
    )
    glue = k8s_glue(
        session,
        pod_name_prefix="cairnml-id-",
        _last_pod_cleanup_per_ns={"ns": 0},
        _min_cleanup_interval_per_ns_sec=0,
        _delete_completed_or_failed_pods=lambda namespace, extra_msg: ["cairnml-id-task-1"],
    )
    from collections import defaultdict

    glue._last_pod_cleanup_per_ns = defaultdict(float)
    assert glue._cleanup_old_pods(["ns"]) == {"ns": ["cairnml-id-task-1"]}
    [read] = session.of("tasks.get_all")
    assert read.json == {
        "id": ["task-1"],
        "status": ["in_progress", "queued"],
        "only_fields": ["id", "status", "status_reason", "last_change"],
        "search_hidden": False,
        "include_subprojects": False,
    }
    [dequeue] = session.of("tasks.dequeue")
    assert dequeue.json == {
        "task": "task-1",
        "status_reason": "Pod deleted (not pending or running)",
        "status_message": "Pod deleted by agent worker-1",
        "remove_from_all_queues": False,
    }
    [failed] = session.of("tasks.failed")
    assert failed.json == {
        "task": "task-1",
        "force": True,
        "status_reason": "Pod deleted (not pending or running)",
        "status_message": "Pod deleted by agent worker-1",
    }


def test_a_task_that_could_not_be_put_back_in_its_queue_is_failed_and_moved_to_the_front():
    session = WireSession(
        {
            "queues.add_task": answer(status=400, msg="no queue"),
            "tasks.failed": answer({"updated": 1}),
            "queues.move_task_to_front": answer({"position": 0}),
        }
    )
    glue = k8s_glue(session)
    glue._re_enqueue_task("queue-1", "task-1")
    [add] = session.of("queues.add_task")
    assert add.json == {"queue": "queue-1", "task": "task-1", "update_execution_queue": True}
    [failed] = session.of("tasks.failed")
    assert failed.json == {
        "task": "task-1",
        "force": True,
        "status_reason": "Task could not be re-enqueued",
        "status_message": "Task could not be re-enqueued",
    }
    [front] = session.of("queues.move_task_to_front")
    assert front.json == {"queue": "queue-1", "task": "task-1"}


def pending_pods(session):
    from cairnml_agent.glue.pending_pods_daemon import PendingPodsDaemon

    daemon = PendingPodsDaemon.__new__(PendingPodsDaemon)
    daemon._session = session
    daemon.log = SimpleNamespace(
        error=lambda *a, **k: None, warning=lambda *a, **k: None, debug=lambda *a, **k: None, info=lambda *a, **k: None
    )
    daemon._last_tasks_msgs = {}
    daemon._agent = SimpleNamespace(k8s_pending_queue_id="pending-q", send_logs=lambda *a, **k: None)
    return daemon


def test_the_pending_pod_of_an_aborted_task_is_found_by_the_stopped_status():
    session = WireSession({"tasks.get_all": answer({"tasks": [{"id": "task-1"}]})})
    daemon = pending_pods(session)
    daemon.get_pods = lambda pod_name=None: []  # the pod is no longer pending: nothing is deleted
    daemon._get_pod_name = lambda pod: "pod-1"
    daemon._handle_aborted_tasks({"task-1": {}, "task-2": {}})
    [call] = session.of("tasks.get_all")
    assert call.json == {
        "id": ["task-1", "task-2"],
        "status": ["stopped"],
        "only_fields": ["id"],
        "search_hidden": False,
        "include_subprojects": False,
    }


def test_the_status_message_of_a_pending_task_is_updated_and_an_in_progress_task_is_enqueued_again():
    session = WireSession(
        {
            "tasks.get_all": answer({"tasks": [{"status": "in_progress"}]}),
            "tasks.enqueue": answer({"queued": 1}),
            "tasks.update": answer({"updated": 1}),
        }
    )
    daemon = pending_pods(session)
    daemon._update_pending_task_msg("task-1", "waiting", ["tag"])
    [read] = session.of("tasks.get_all")
    # the id is a string (when the call is built by hand) or a one-item list (a generated class): the server reads both
    assert read.json == {
        "id": ["task-1"],
        "only_fields": ["status"],
        "search_hidden": False,
        "include_subprojects": False,
    }
    [enqueue] = session.of("tasks.enqueue")
    assert enqueue.json == {"task": "task-1", "force": True, "queue": "pending-q"}
    [update] = session.of("tasks.update")
    assert update.json == {"task": "task-1", "status_message": "K8S glue status: waiting", "tags": ["tag"]}
    assert daemon._last_tasks_msgs == {"task-1": ("waiting", ["tag"])}


def test_a_refused_status_message_update_is_logged_and_not_remembered():
    session = WireSession(
        {
            "tasks.get_all": answer({"tasks": [{"status": "queued"}]}),
            "tasks.update": answer(status=400, msg="refused"),
        }
    )
    daemon = pending_pods(session)
    warnings = []
    daemon.log.warning = warnings.append
    daemon._update_pending_task_msg("task-1", "waiting")
    assert warnings and daemon._last_tasks_msgs == {}

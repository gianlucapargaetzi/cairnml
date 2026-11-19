"""What Worker.execute does for a host-mode task, pinned before it is split (issue #46, slice 1).

The worker is a stub that records each call, and the command is recorded rather than run:
the monitored run returns the exit code it is given, the direct run records its call and
exits with that code. The kill of leftover processes is stubbed. What is pinned: the order
of the steps, the command that is built, the exit code and status calls that follow the run.
"""

from types import SimpleNamespace

import pytest

from cairnml.backend_api.services import tasks as tasks_api

from cairnml_agent.commands import worker as worker_module
from cairnml_agent.commands.worker import Worker


class Argv:
    def __init__(self, calls, exit_code, *argv):
        self.calls = calls
        self.exit_code = exit_code
        self.argv = list(argv)

    def pretty(self):
        return " ".join(str(a) for a in self.argv)

    def update_envs(self, envs):
        pass

    def check_call(self, cwd=None):
        self.calls.append(("check_call", self.argv, cwd))
        return self.exit_code


class PackageApi:
    def __init__(self, calls, exit_code):
        self.calls = calls
        self.exit_code = exit_code

    def get_python_command(self, extra):
        return Argv(self.calls, self.exit_code, "python3", *extra)


class Session:
    """The agent's session as the host path uses it: the task lookup, the started mark and the dequeue answer."""

    def __init__(self, calls, task):
        self.calls = calls
        self._task = task
        self.config = {"agent.venvs_dir": "/tmp/unused-venvs"}
        self.config_file = "/tmp/unused.conf"
        self.debug_mode = False
        self.trace = False

    def print_configuration(self):
        self.calls.append(("print_configuration",))

    def current(self, request_cls):
        return request_cls

    def send_api(self, request):
        if isinstance(request, tasks_api.GetByIdRequest):
            return SimpleNamespace(task=self._task)
        if isinstance(request, tasks_api.StartedRequest):
            self.calls.append(("tasks.started", request.status_reason))
            return SimpleNamespace()
        raise AssertionError("no answer for {}".format(type(request).__name__))

    def send(self, request):
        return SimpleNamespace(ok=lambda: True, response_data={"updated": 0})

    def send_request(self, **kwargs):
        return SimpleNamespace(ok=True, json=lambda: {"data": {"updated": 0}})


class Log:
    def debug(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
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

    def __init__(self, calls, task, code_dir, exit_code=0):
        self.calls = calls
        self._session = Session(calls, task)
        self._standalone_mode = None
        self._compiled = False
        self._bootstrap = False
        self._default_pip = None
        self.temp_config_path = None
        self.is_conda = False
        self.uv = SimpleNamespace(enabled=False)
        self.package_api = PackageApi(calls, exit_code)
        self.log = Log()
        self._polling_interval = 1
        self._task_logging_pass_control_message = "Running task id [{}]:"
        self._task_status_change_message = "status"
        self._code_dir = code_dir
        self._exit_code = exit_code

    def _get_task_python_version(self, task):
        return "3.12"

    def _get_node_rank(self):
        return 0

    def get_execution_info(self, task):
        return SimpleNamespace(repository=None, entry_point="train.py", working_dir=".", binary="python3")

    def install_virtualenv(self, **kwargs):
        self.calls.append(("install_virtualenv", kwargs["requested_python_version"]))
        return "/venvs/3.12", None, False, "3.12"

    def get_repo_info(self, execution, task, venv_folder):
        return self._code_dir, None, None

    def freeze_task_environment(self, **kwargs):
        self.calls.append(("freeze",))
        return {"numpy": "2.0"}

    def report_monitor(self, report):
        self.calls.append(("report_monitor",))

    def stop_monitor(self):
        self.calls.append(("stop_monitor",))

    def _unregister(self):
        self.calls.append(("_unregister",))

    def _log_command_output(self, task_id, cmd, stdout_path, cwd):
        self.calls.append(("run", cmd.argv, cwd))
        return self._exit_code, None

    def _update_commit_id(self, *args):
        pass

    def _get_job_os_envs(self, task, log_level):
        return {}

    def _get_task_os_env(self, config, task):
        return {}

    def handle_task_termination(self, task_id, exit_code, stop_reason, session=None):
        self.calls.append(("handle_task_termination", exit_code))

    def set_docker_variables(self, *args, **kwargs):
        self.calls.append(("set_docker_variables",))


def task():
    return SimpleNamespace(
        id="task-1", name="train", script=SimpleNamespace(binary="python3", requirements=None, entry_point=None)
    )


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setattr(worker_module, "kill_all_child_processes", lambda *a, **k: None)
    monkeypatch.setattr(worker_module, "get_python_path", lambda *a, **k: "/work/code:/work/lib")
    monkeypatch.setattr(worker_module, "ENV_TASK_EXECUTE_AS_USER", SimpleNamespace(get=lambda: False))
    monkeypatch.setattr(worker_module, "ENV_AGENT_SKIP_PYTHON_ENV_INSTALL", SimpleNamespace(get=lambda: False))
    monkeypatch.setattr(worker_module, "ENV_AGENT_FORCE_EXEC_SCRIPT", SimpleNamespace(get=lambda: None))
    monkeypatch.setattr(worker_module, "ENV_AGENT_FORCE_TASK_INIT", SimpleNamespace(get=lambda: False))
    monkeypatch.setattr(worker_module, "ENV_CUSTOM_BUILD_SCRIPT", SimpleNamespace(get=lambda: None))
    monkeypatch.setattr(worker_module, "ENV_TASK_EXTRA_PYTHON_PATH", SimpleNamespace(get=lambda: None))
    monkeypatch.setattr(worker_module, "ENV_TEMP_STDOUT_FILE_DIR", SimpleNamespace(get=lambda: None))
    monkeypatch.setattr(worker_module, "safe_mkstemp", lambda **kwargs: "/tmp/unused-out.txt")
    monkeypatch.setattr(worker_module, "is_linux_platform", lambda: False)


def test_a_host_task_runs_in_the_order_the_worker_has_always_used(host, tmp_path):
    calls = []
    status = Worker.execute(
        Recorder(calls, task(), str(tmp_path)), task_id="task-1", log_level="INFO", standalone_mode=False
    )
    assert status == 0
    assert [c[0] for c in calls] == [
        "print_configuration",
        "tasks.started",  # "worker started execution", before the environment is built
        "report_monitor",
        "install_virtualenv",
        "tasks.started",  # "worker starting task execution", just before the run
        "run",
        "handle_task_termination",
        "stop_monitor",
        "_unregister",
    ]


def test_no_freeze_is_taken_for_a_task_with_no_requirements_and_no_repository(host, tmp_path):
    calls = []
    Worker.execute(Recorder(calls, task(), str(tmp_path)), task_id="task-1", log_level="INFO", standalone_mode=False)
    assert ("freeze",) not in calls


def test_the_command_is_the_script_with_unbuffered_python_run_in_the_code_directory(host, tmp_path):
    calls = []
    Worker.execute(Recorder(calls, task(), str(tmp_path)), task_id="task-1", log_level="INFO", standalone_mode=False)
    run = next(c for c in calls if c[0] == "run")
    assert run[1] == ["python3", "-u", "train.py"]
    assert run[2] == str(tmp_path)


def test_the_exit_code_of_the_run_is_reported_and_returned(host, tmp_path):
    calls = []
    status = Worker.execute(
        Recorder(calls, task(), str(tmp_path), exit_code=3), task_id="task-1", log_level="INFO", standalone_mode=False
    )
    assert status == 3
    assert ("handle_task_termination", 3) in calls


def test_without_monitoring_the_command_runs_directly_and_the_process_exits_with_its_code(host, tmp_path):
    calls = []
    stub = Recorder(calls, task(), str(tmp_path), exit_code=5)
    with pytest.raises(SystemExit) as exit_info:
        Worker.execute(stub, task_id="task-1", log_level="INFO", standalone_mode=False, disable_monitoring=True)
    assert exit_info.value.code == 5
    assert ("check_call", ["python3", "-u", "train.py"], str(tmp_path)) in calls
    # no task termination, no monitor stop, no unregister: the caller owns those
    assert not any(c[0] in ("handle_task_termination", "stop_monitor", "_unregister") for c in calls)

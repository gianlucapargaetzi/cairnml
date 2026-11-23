"""Worker._run_in_worker in services mode (full monitoring) writes the agent's configuration for the job.

The agent no longer loads the server's vaults, so the session holds only the configuration the agent started with and
that is what the job file gets. The session of the agent has no ``pre_vault_config`` (the vault loading that set it is
gone); the stub session here has none either, so a read of it fails like it does on a real session.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from pyhocon import ConfigFactory

from cairnml_agent.commands import worker as worker_module
from cairnml_agent.commands.worker import Worker


class Recorder:
    _run_in_worker = Worker._run_in_worker
    dump_config = Worker.dump_config

    def __init__(self, session):
        self._session = session
        self._compiled = False
        self.temp_config_path = None
        self.calls = []

    def _singleton(self):
        self.calls.append("_singleton")

    def report_monitor(self, report):
        self.calls.append("report_monitor")

    def run_one_task(self, queue, task_id, worker_args, docker=None, task_session=None):
        # the file the job is started with exists while the task runs
        self.config_seen_by_the_job = json.loads(Path(worker_args.config_file).read_text())
        self.config_file_seen_by_the_job = worker_args.config_file
        return 0

    def stop_monitor(self):
        self.calls.append("stop_monitor")

    def _unregister(self):
        self.calls.append("_unregister")


class Session:
    """What the worker reads of the session; like the real one, config_file is the file set through _config_file."""

    def __init__(self):
        self.config = ConfigFactory.parse_string('agent.worker_id = "w-1"\napi.host = "https://api.example"')
        self._config_file = "/tmp/unused.conf"
        self.debug_mode = False
        self.trace = False

    @property
    def config_file(self):
        return self._config_file


def test_services_mode_dumps_the_session_configuration_for_the_job(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_module, "ENV_TEMP_STDOUT_FILE_DIR", SimpleNamespace(get=lambda: str(tmp_path)))
    monkeypatch.setattr(worker_module.Singleton, "close_pid_file", lambda: None)
    worker = Recorder(Session())

    status = worker._run_in_worker(
        SimpleNamespace(id="task-1"), "task-1", log_level=None, docker=None, full_monitoring=True
    )

    assert status == 0
    assert worker.config_seen_by_the_job == {"agent": {"worker_id": ""}, "api": {"host": "https://api.example"}}
    assert worker.config_file_seen_by_the_job.startswith(str(tmp_path))
    # the file is removed again when the task is over
    assert not Path(worker.config_file_seen_by_the_job).exists()

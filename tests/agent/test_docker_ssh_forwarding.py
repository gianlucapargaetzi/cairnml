"""The agent passes its SSH agent socket into a job container; a task's own -v is still stripped (issue #182)."""

from types import SimpleNamespace

import pytest

from cairnml_agent.commands import worker as worker_module

from .test_docker_command_characterisation import common, run_args, stub_worker  # noqa: F401 (pytest fixture)
from .test_docker_forbidden_switches import DEFAULTS, _Config

pytestmark = pytest.mark.usefixtures("common")

SOCK = "/run/user/1000/keyring/ssh"
FORWARDING = ["-v", "{0}:{0}".format(SOCK), "-e", "SSH_AUTH_SOCK={}".format(SOCK)]


def _worker(config=None):
    worker = stub_worker()
    worker._session = SimpleNamespace(config=_Config(DEFAULTS) if config is None else config)
    worker._docker_args_filters = []
    return worker


def _adjacent(cmd, first, second):
    return any(cmd[i] == first and cmd[i + 1] == second for i in range(len(cmd) - 1))


def _warnings(worker):
    return [c.args[0] for c in worker.log.warning.call_args_list]


def test_the_agent_socket_is_mounted_and_no_warning():
    worker = _worker()
    cmd = worker_module.Worker._get_docker_cmd(worker, run_args(ssh_forwarding_args=list(FORWARDING)))
    assert _adjacent(cmd, "-v", "{0}:{0}".format(SOCK))
    assert _adjacent(cmd, "-e", "SSH_AUTH_SOCK={}".format(SOCK))
    assert _warnings(worker) == []


def test_a_task_v_is_still_stripped_with_the_warning():
    worker = _worker()
    cmd = worker_module.Worker._get_docker_cmd(
        worker, run_args(docker_arguments=["-v", "/:/host"], ssh_forwarding_args=list(FORWARDING))
    )
    assert "/:/host" not in cmd
    assert _adjacent(cmd, "-v", "{0}:{0}".format(SOCK))
    warnings = _warnings(worker)
    assert len(warnings) == 1 and "-v" in warnings[0]


def test_a_task_argument_with_the_same_socket_is_stripped():
    worker = _worker()
    cmd = worker_module.Worker._get_docker_cmd(
        worker,
        run_args(docker_arguments=["-v", "{0}:{0}".format(SOCK)], ssh_forwarding_args=list(FORWARDING)),
    )
    # only the agent's own mount is left: the task's copy of the same string is removed
    assert cmd.count("{0}:{0}".format(SOCK)) == 1
    assert _adjacent(cmd, "-v", "{0}:{0}".format(SOCK))
    warnings = _warnings(worker)
    assert len(warnings) == 1 and "-v" in warnings[0]


def test_an_admin_extra_v_is_still_stripped():
    worker = _worker()
    cmd = worker_module.Worker._get_docker_cmd(
        worker,
        run_args(extra_docker_arguments=["-v", "/:/host"], ssh_forwarding_args=list(FORWARDING)),
    )
    assert "/:/host" not in cmd
    assert _adjacent(cmd, "-v", "{0}:{0}".format(SOCK))
    assert any("-v" in w for w in _warnings(worker))

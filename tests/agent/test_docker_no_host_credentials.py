"""A job container gets nothing from the host's SSH or git setup (cairnml#194).

Not the SSH agent socket, not a copy of ~/.ssh, not ~/.git-credentials, not ~/.gitconfig. The job's repository is read
with the token the server issues for it, or anonymously (see test_git_clone_credentials.py).
"""

import dataclasses
import socket
from types import SimpleNamespace

import pytest

from cairnml_agent.backend_config.config import Config
from cairnml_agent.commands import worker as worker_module
from cairnml_agent.commands.docker_run import DockerRun

from .test_docker_command_characterisation import common, run_args, stub_worker  # noqa: F401 (pytest fixture)

pytestmark = pytest.mark.usefixtures("common")

CREDENTIAL_NAMES = ("/.ssh", "ssh_ro", "cairnml_agent.ssh", ".gitconfig", ".git-credentials", "/.git/", ".ssh_ro")


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A host with an SSH agent socket, ~/.ssh, ~/.git-credentials and ~/.gitconfig, as on a developer's machine."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE KEY\n")
    (home / ".git-credentials").write_text("https://user:secret@github.com\n")
    (home / ".gitconfig").write_text("[credential]\n\thelper = store\n")
    sock_path = tmp_path / "agent.sock"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(sock_path))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SSH_AUTH_SOCK", str(sock_path))
    monkeypatch.delenv("CAIRNML_AGENT_DISABLE_SSH_MOUNT", raising=False)
    yield SimpleNamespace(home=home, sock=str(sock_path))
    listener.close()


def _worker(tmp_path):
    """A worker with the real docker configuration methods, on the agent's default configuration."""
    import inspect
    import types

    worker = stub_worker()
    for name in (
        "get_docker_config_cmd",
        "_get_docker_config_cmd",
        "_get_docker_cmd",
        "remove_non_backwards_compatible_entries",
        "dump_config",
    ):
        setattr(worker, name, types.MethodType(inspect.getattr_static(worker_module.Worker, name), worker))
    worker._session = SimpleNamespace(config=Config(config_folder=None, verbose=False))
    worker._session.config.put("agent.vcs_cache.path", str(tmp_path / "vcs"))
    worker._session.config.put("agent.venvs_cache.path", str(tmp_path / "venvs"))
    worker._session.config.put("agent.pip_download_cache.path", str(tmp_path / "pip"))
    worker._session.config.put("sdk.storage.cache.default_base_dir", str(tmp_path / "cache"))
    worker._session.config.put("agent.docker_apt_cache", str(tmp_path / "apt"))
    worker._session.config.put("agent.docker_pip_cache", str(tmp_path / "pipc"))
    worker._standalone_mode = False
    worker._docker_fixed_user_cache = "/cairnml_agent_cache"
    worker._docker_default_cmd_override = False
    worker._extra_docker_arguments = None
    worker._extra_shell_script = None
    worker._temp_cleanup_list = []
    worker._force_current_version = None
    worker._docker_args_filters = []
    worker.temp_config_path = str(tmp_path / "agent.cfg")
    worker.worker_id = "w1"
    worker.parent_worker_id = None
    worker.poetry = SimpleNamespace(enabled=False)
    worker.uv = SimpleNamespace(enabled=False)
    return worker


def _command_of(worker):
    _, build = worker.get_docker_config_cmd(["img:1"])
    return build(docker_image="img:1", docker_arguments=[], docker_bash_setup_script=None)


def _text(cmd):
    return " ".join(str(word) for word in cmd)


def test_the_agent_socket_is_neither_mounted_nor_named(host, tmp_path):
    cmd = _command_of(_worker(tmp_path))
    assert host.sock not in _text(cmd)
    assert "SSH_AUTH_SOCK" not in _text(cmd)


def test_no_copy_of_the_ssh_folder_or_the_git_files_is_mounted(host, tmp_path):
    worker = _worker(tmp_path)
    cmd = _command_of(worker)
    mounts = [cmd[i + 1] for i, word in enumerate(cmd[:-1]) if word == "-v"]
    sources_and_targets = [part for mount in mounts for part in mount.split(":")[:2]]
    assert not any(name in part for part in sources_and_targets for name in CREDENTIAL_NAMES), mounts
    assert str(host.home) not in _text(cmd)
    # the old copy was made into a temporary folder of this name and mounted from there
    assert not [p for p in worker._temp_cleanup_list if "cairnml_agent.ssh" in str(p)]


def test_the_container_script_copies_no_ssh_or_git_files(host, tmp_path):
    script = _text(_command_of(_worker(tmp_path)))
    assert "/.ssh" not in script and "ssh_ro" not in script
    assert "cp -Rf" not in script or ".git" not in script.split("cp -Rf", 1)[1][:200]


def test_nothing_in_the_job_configuration_carries_a_git_credential(host, tmp_path):
    worker = _worker(tmp_path)
    worker._session.config.put("agent.git_user", "bob")
    worker._session.config.put("agent.git_pass", "hunter2")
    _command_of(worker)
    written = (tmp_path / "agent.cfg").read_text()
    assert "hunter2" not in written and "bob" not in written


def test_the_docker_run_description_has_no_field_for_them():
    fields = {field.name for field in dataclasses.fields(DockerRun)}
    assert not fields & {"host_ssh_cache", "ssh_forwarding_args", "host_git_credentials", "mount_ssh", "mount_ssh_ro"}


def test_an_administrators_own_extra_arguments_still_reach_the_command(host, tmp_path):
    worker = _worker(tmp_path)
    worker._extra_docker_arguments = ["-e", "FROM_ADMIN=1"]
    cmd = _command_of(worker)
    assert any(cmd[i] == "-e" and cmd[i + 1] == "FROM_ADMIN=1" for i in range(len(cmd) - 1))


def test_run_args_still_build_a_command_without_them():
    cmd = worker_module.Worker._get_docker_cmd(stub_worker(), run_args())
    assert cmd[:2] == ["docker", "run"]

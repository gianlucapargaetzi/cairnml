"""What Worker._get_docker_cmd builds for the bootstrap and the kubernetes branches, pinned before it is split
(issue #46, slice 3c). The plain docker path is pinned by tests/test_format_calls.py."""

import dataclasses
from types import SimpleNamespace
from unittest import mock

import pytest

from cairnml_agent.commands import worker as worker_module
from cairnml_agent.commands.docker_run import DockerRun


def bind_real_methods(stub):
    """Give a stub worker the real helpers that the docker command builder calls on self."""
    import inspect
    import types

    for name in dir(worker_module.Worker):
        if name.startswith("_docker_") or name in (
            "_filter_docker_args",
            "_amd_gpu_docker_args",
            "_resolve_docker_env_args",
        ):
            raw = inspect.getattr_static(worker_module.Worker, name)
            if not (inspect.isfunction(raw) or isinstance(raw, staticmethod)):
                continue
            if isinstance(raw, staticmethod):
                setattr(stub, name, raw.__func__)
            else:
                setattr(stub, name, types.MethodType(raw, stub))
    return stub


def stub_worker(bootstrap=False):
    return bind_real_methods(
        SimpleNamespace(
            debug=lambda *args, **kwargs: None,
            log=mock.MagicMock(),
            _bootstrap=bootstrap,
            _services_mode=False,
            _server_is_loopback=lambda: False,
            _worker_label="cairnml-worker={}",
            _parent_worker_label="cairnml-parent-worker={}",
            _session=SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: default)),
            _use_podman=False,
        )
    )


def run_args(**overrides):
    args = {field.name: None for field in dataclasses.fields(DockerRun)}
    args.update(
        worker_id="w1",
        parent_worker_id="p1",
        docker_image="img:1",
        python_version="3.11",
        conf_file="/tmp/none.conf",
        standalone_mode=True,
        mounted_cache="/mnt/cache",
        mounted_pip_dl="/mnt/pip_dl",
        mounted_vcs_cache="/mnt/vcs",
        mounted_venvs_cache="/mnt/venvs",
        extra_shell_script="",
    )
    args.update(overrides)
    return DockerRun(**args)


@pytest.fixture
def common(monkeypatch):
    monkeypatch.setenv("NVIDIA_VISIBLE_DEVICES", "all")
    monkeypatch.setattr(worker_module, "detect_rocm_env", lambda: False)
    monkeypatch.setattr(worker_module, "local_agent_wheel", lambda: "/x/agent.whl")
    monkeypatch.setattr(worker_module, "local_sdk_wheel", lambda: "/x/sdk.whl")
    monkeypatch.setattr(worker_module, "ENV_DOCKER_SKIP_GPUS_FLAG", SimpleNamespace(get=lambda: False))
    monkeypatch.setattr(worker_module, "ENV_DOCKER_HOST_MOUNT", SimpleNamespace(get=lambda: None))


def test_the_bootstrap_branch_passes_the_bootstrap_configuration_and_runs_the_bootstrap_script(common, monkeypatch):
    monkeypatch.setattr(
        worker_module.bootstrap,
        "get_bootstrap_docker_configuration",
        lambda config: ["-e", "CAIRNML_BOOTSTRAP_DIR=/opt/boot"],
    )
    cmd = worker_module.Worker._get_docker_cmd(
        stub_worker(bootstrap=True),
        run_args(
            standalone_mode=False,
            preprocess_bash_script=["echo pre"],
            extra_shell_script="echo x ; ",
        ),
    )
    assert cmd == [
        "docker",
        "run",
        "-t",
        "--gpus",
        "all",
        "-l",
        "cairnml-worker=w1",
        "-l",
        "cairnml-parent-worker=p1",
        "-e",
        "CAIRNML_WORKER_ID=w1",
        "-e",
        "CAIRNML_DOCKER_IMAGE=img:1",
        "-v",
        "/x/agent.whl:/tmp/agent.whl",
        "-v",
        "/x/sdk.whl:/tmp/sdk.whl",
        "-e",
        "CAIRNML_AGENT_LOCAL_SDK_WHEEL=/tmp/sdk.whl",
        "-e",
        "CAIRNML_BOOTSTRAP_DIR=/opt/boot",
        "-e",
        "CAIRNML_BOOTSTRAP_CUSTOM_CMD_BASE64=ZWNobyBwcmU=",
        "-e",
        "CAIRNML_PRE_CUSTOM_CMD=echo x ; ",
        "-e",
        "CAIRNML_AGENT_VERSION=/tmp/agent.whl",
        "-v",
        "/tmp/none.conf:/tmp/cairnml.conf",
        "-e",
        "CAIRNML_CONFIG_FILE=/tmp/cairnml.conf",
        "--rm",
        "img:1",
        "bash",
        "-c",
        "$CAIRNML_BOOTSTRAP_DIR/bootstrap.sh ",
    ]


def test_the_kubernetes_branch_maps_host_folders_to_the_node_path(common, monkeypatch, tmp_path):
    pod = tmp_path / "pod"
    pod.mkdir()
    node = tmp_path / "node"
    monkeypatch.setattr(worker_module, "ENV_DOCKER_HOST_MOUNT", SimpleNamespace(get=lambda: f"{node}:{pod}"))
    conf = tmp_path / "agent.conf"
    conf.write_text("api {}\n")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("KUBERNETES_PORT", "443")
    monkeypatch.setattr(worker_module, "get_bash_output", lambda cmd: "bridge")
    cmd = worker_module.Worker._get_docker_cmd(
        stub_worker(),
        run_args(
            conf_file=str(conf),
            host_apt_cache=f"{pod}/apt",
            host_pip_cache=f"{pod}/pip",
        ),
    )
    node_path = str(node)
    assert cmd == [
        "docker",
        "run",
        "-t",
        "--gpus",
        "all",
        "-l",
        "cairnml-worker=w1",
        "-l",
        "cairnml-parent-worker=p1",
        "--network",
        "bridge",
        "-e",
        "NVIDIA_VISIBLE_DEVICES=all",
        "-e",
        "CAIRNML_WORKER_ID=w1",
        "-e",
        "CAIRNML_DOCKER_IMAGE=img:1",
        "-v",
        "/x/agent.whl:/tmp/agent.whl",
        "-v",
        "/x/sdk.whl:/tmp/sdk.whl",
        "-e",
        "CAIRNML_AGENT_LOCAL_SDK_WHEEL=/tmp/sdk.whl",
        "-v",
        f"{node_path}/.cairnml_agent.w1.cfg:/tmp/cairnml.conf",
        "-e",
        "CAIRNML_CONFIG_FILE=/tmp/cairnml.conf",
        "-v",
        f"{node_path}/apt:/var/cache/apt/archives",
        "-v",
        f"{node_path}/pip:/root/.cache/pip",
        "--rm",
        "img:1",
        "bash",
        "-c",
        "cp /tmp/cairnml.conf ~/default_cairnml.conf ; NVIDIA_VISIBLE_DEVICES=all $LOCAL_PYTHON -u -m cairnml_agent ",
    ]

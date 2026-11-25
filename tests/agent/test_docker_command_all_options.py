"""The docker command of a task with every optional input set, pinned before _get_docker_cmd is split (issue #46)."""

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


def stub_worker():
    return bind_real_methods(
        SimpleNamespace(
            debug=lambda *args, **kwargs: None,
            log=mock.MagicMock(),
            _bootstrap=False,
            _services_mode=True,
            _server_is_loopback=lambda: True,
            _worker_label="cairnml-worker={}",
            _parent_worker_label="cairnml-parent-worker={}",
            _session=SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: default)),
            _use_podman=False,
            _docker_args_filters=[],
            send_logs=lambda *a, **k: None,
        )
    )


@pytest.fixture
def everything(monkeypatch, tmp_path):
    monkeypatch.setenv("NVIDIA_VISIBLE_DEVICES", "0,1")
    monkeypatch.setattr(worker_module, "detect_rocm_env", lambda: False)
    monkeypatch.setattr(worker_module, "local_agent_wheel", lambda: "/x/agent.whl")
    monkeypatch.setattr(worker_module, "local_sdk_wheel", lambda: "/x/sdk.whl")
    monkeypatch.setattr(worker_module, "ENV_DOCKER_SKIP_GPUS_FLAG", SimpleNamespace(get=lambda: False))
    monkeypatch.setattr(worker_module, "ENV_DOCKER_HOST_MOUNT", SimpleNamespace(get=lambda: None))
    monkeypatch.setattr(
        worker_module, "ENV_AGENT_SKIP_PIP_VENV_INSTALL", SimpleNamespace(get=lambda: "1", vars=["CAIRNML_SKIP"])
    )
    monkeypatch.setattr(worker_module, "ENV_EXTRA_DOCKER_LABELS", SimpleNamespace(get=lambda: ["team=ml"]))
    monkeypatch.setattr(worker_module, "ENV_WORKER_TAGS", SimpleNamespace(vars=["CAIRNML_TAGS"]))
    monkeypatch.setattr(worker_module, "ENV_AGENT_AUTH_TOKEN", SimpleNamespace(vars=["CAIRNML_AUTH"]))
    conf = tmp_path / "agent.conf"
    conf.write_text("api {}\n")
    return SimpleNamespace(conf=str(conf), token_dir=str(tmp_path / "token"))


def test_every_optional_input_gives_the_same_command(everything, tmp_path):
    tmp = str(tmp_path)
    args = {field.name: None for field in dataclasses.fields(DockerRun)}
    args.update(
        worker_id="w1",
        parent_worker_id="p1",
        docker_image="img:2",
        python_version="3.10",
        conf_file=everything.conf,
        standalone_mode=False,
        docker_arguments=["--shm-size=1g"],
        extra_docker_arguments=["-e", "A=1"],
        extra_shell_script="echo extra ; ",
        host_apt_cache="/h/apt",
        host_pip_cache="/h/pip",
        host_poetry_cache="/h/poetry",
        host_cache="/h/cache",
        mounted_cache="/mnt/cache",
        host_pip_dl="/h/dl",
        mounted_pip_dl="/mnt/pip_dl",
        host_vcs_cache="/h/vcs",
        mounted_vcs_cache="/mnt/vcs",
        host_venvs_cache="/h/venvs",
        mounted_venvs_cache="/mnt/venvs",
        preprocess_bash_script=["export X=1"],
        install_opencv_libs=True,
        docker_bash_setup_script="# comment\nexport Y=2\n\nexport Z=3\n",
        auth_token="tok",
        job_token_dir=everything.token_dir,
        worker_tags=["gpu", "big"],
        name="job-container",
        env_task_id="task-9",
        restart="on-failure:3",
    )
    cmd = worker_module.Worker._get_docker_cmd(stub_worker(), DockerRun(**args))
    assert cmd == [
        "docker",
        "run",
        "-t",
        "--restart",
        "on-failure:3",
        "--gpus",
        '"device=0,1"',
        "-e",
        "A=1",
        "--shm-size=1g",
        "-l",
        "cairnml-worker=w1",
        "-l",
        "cairnml-parent-worker=p1",
        "-l",
        "team=ml",
        "--network",
        "host",
        "-e",
        "CAIRNML_WORKER_ID=w1",
        "-e",
        "CAIRNML_DOCKER_IMAGE=img:2",
        "-e",
        "CAIRNML_TASK_ID=task-9",
        "-e",
        "CAIRNML_AUTH=tok",
        "-v",
        tmp + "/token:/root/.cairnml/job-token",
        "-e",
        "CAIRNML_JOB_TOKEN_FILE=/root/.cairnml/job-token/job_token",
        "-e",
        "CAIRNML_TAGS=gpu big",
        "-e",
        "CAIRNML_SKIP=1",
        "-e",
        "CAIRNML_AGENT_SERVICE_TASK=1",
        "-v",
        "/x/agent.whl:/tmp/agent.whl",
        "-v",
        "/x/sdk.whl:/tmp/sdk.whl",
        "-e",
        "CAIRNML_AGENT_LOCAL_SDK_WHEEL=/tmp/sdk.whl",
        "--name",
        "job-container",
        "-v",
        tmp + "/agent.conf:/tmp/cairnml.conf",
        "-e",
        "CAIRNML_CONFIG_FILE=/tmp/cairnml.conf",
        "-v",
        "/h/apt:/var/cache/apt/archives",
        "-v",
        "/h/pip:/root/.cache/pip",
        "-v",
        "/h/poetry:/root/.cache/pypoetry",
        "-v",
        "/h/dl:/mnt/pip_dl",
        "-v",
        "/h/cache:/mnt/cache",
        "-v",
        "/h/vcs:/mnt/vcs",
        "-v",
        "/h/venvs:/mnt/venvs",
        "img:2",
        "bash",
        "-c",
        'export X=1 ; echo \'Binary::apt::APT::Keep-Downloaded-Packages "true";\' > /etc/apt/apt.conf.d/docker-clean ; chown -R $(whoami) $HOME/.cache/pip ; export DEBIAN_FRONTEND=noninteractive ; export CAIRNML_APT_INSTALL="$CAIRNML_APT_INSTALL libsm6 libxext6 libxrender-dev libglib2.0-0" ; [ ! -z $(which git 2> /dev/null || command -v git) ] || export CAIRNML_APT_INSTALL="$CAIRNML_APT_INSTALL git" ; declare LOCAL_PYTHON ; [ ! -z $LOCAL_PYTHON ] || for i in {20..5}; do (which 3.$i 2> /dev/null || command -v 3.$i) && 3.$i -m pip --version && export LOCAL_PYTHON=$(which 3.$i 2> /dev/null || command -v 3.$i) && break ; done ; [ ! -z $LOCAL_PYTHON ] || export CAIRNML_APT_INSTALL="$CAIRNML_APT_INSTALL 3-pip" ; [ -z "$CAIRNML_APT_INSTALL" ] || (apt-get update -y ; apt-get install -y $CAIRNML_APT_INSTALL) || (dnf install -y $CAIRNML_APT_INSTALL) ; rm -f /usr/lib/python3.*/EXTERNALLY-MANAGED ; [ ! -z $LOCAL_PYTHON ] || export LOCAL_PYTHON=3.10 ; $LOCAL_PYTHON -m pip --version > /dev/null || export LOCAL_PYTHON=$(PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin command -v python3) ; $LOCAL_PYTHON -m pip install -U "pip" ; $LOCAL_PYTHON -m pip install -U "$CAIRNML_AGENT_LOCAL_SDK_WHEEL" ; $LOCAL_PYTHON -m pip install -U "/tmp/agent.whl[agent]" ; echo extra ; export Y=2 ; export Z=3 ; cp /tmp/cairnml.conf ~/default_cairnml.conf ; NVIDIA_VISIBLE_DEVICES=all $LOCAL_PYTHON -u -m cairnml_agent ',
    ]

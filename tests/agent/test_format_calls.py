"""Format calls that dropped a value, or did not match their template, as pyflakes and the tests below see them."""

import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
FINDING = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+):\d+: (?P<message>.*)$")
FORMAT_CALL = re.compile(r"\.format\(|^'.*' % ")
FORMAT_PROBLEM = (
    "unused argument",
    "unused named argument",
    "missing argument",
    "placeholder",
    "invalid format string",
)
EXCLUDED = ("_vendor", "/external/", "backend_api/services")


def test_no_format_call_drops_or_misses_a_value():
    run = subprocess.run([sys.executable, "-m", "pyflakes", "cairnml_agent"], cwd=ROOT, capture_output=True, text=True)
    assert run.returncode in (0, 1), run.stderr
    assert run.stderr == "", run.stderr
    offenders = []
    for line in run.stdout.split("\n"):
        match = FINDING.match(line)
        if not match:
            continue
        message = match["message"]
        if not FORMAT_CALL.search(message) or not any(word in message for word in FORMAT_PROBLEM):
            continue
        if any(part in Path(match["path"]).as_posix() for part in EXCLUDED):
            continue
        offenders.append("{}:{} {}".format(match["path"], match["line"], message))
    assert offenders == []


def test_a_failed_cache_rename_logs_the_error(tmp_path, monkeypatch, caplog):
    import logging

    from cairnml_agent.helper.os import folder_cache
    from cairnml_agent.helper.os.folder_cache import FolderCache

    cache = FolderCache(tmp_path / "cache")
    src = tmp_path / "src"
    src.mkdir()
    (src / "f.txt").write_text("x")
    assert cache.add_entry(["a"], src)

    def boom(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(folder_cache.shutil, "move", boom)
    with caplog.at_level(logging.WARNING):
        assert cache.add_entry(["a", "b"], src) is True
    text = caplog.text
    assert "Could not rename cache entry" in text
    assert "boom" in text
    assert "a.b" in text


def test_a_failed_template_argument_prints_the_error(monkeypatch, capsys):
    from cairnml_agent.helper.custom_template import CustomTemplate
    from cairnml_agent.helper.docker_args import DockerArgsTemplateResolver

    def boom(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(CustomTemplate, "default_custom_substitute", boom)
    monkeypatch.setattr(CustomTemplate, "get_queue_name", lambda *args, **kwargs: "q")
    session = SimpleNamespace(declares=lambda service, action, field: True, config=None)
    resolver = DockerArgsTemplateResolver(session, "t1", "q1", "q", "w1", task_info={})
    resolver.resolve_from_docker_cmd(["-e", "${CAIRNML_x}", "image"])
    out = capsys.readouterr().out
    assert "Failed parsing" in out
    assert "boom" in out
    assert "error ()" not in out


def _bound(stub):
    """Give the stub worker the real helpers that the docker command builder calls on self."""
    import inspect
    import types

    from cairnml_agent.commands import worker as worker_module

    for name in dir(worker_module.Worker):
        if name.startswith("_docker_") or name in (
            "_filter_docker_args",
            "_amd_gpu_docker_args",
            "_resolve_docker_env_args",
        ):
            raw = inspect.getattr_static(worker_module.Worker, name)
            if not (inspect.isfunction(raw) or isinstance(raw, staticmethod)):
                continue
            setattr(stub, name, raw.__func__ if isinstance(raw, staticmethod) else types.MethodType(raw, stub))
    return stub


def test_the_docker_command_string_is_unchanged(monkeypatch):
    from unittest import mock

    from cairnml_agent.commands import worker as worker_module

    monkeypatch.setenv("NVIDIA_VISIBLE_DEVICES", "all")
    monkeypatch.setattr(worker_module, "detect_rocm_env", lambda: False)
    monkeypatch.setattr(worker_module, "local_agent_wheel", lambda: "/x/agent.whl")
    monkeypatch.setattr(worker_module, "local_sdk_wheel", lambda: "/x/sdk.whl")
    import dataclasses

    arguments = {field.name: None for field in dataclasses.fields(worker_module.DockerRun)}
    arguments.update(
        worker_id="w1",
        parent_worker_id="p1",
        docker_image="img:1",
        python_version="3.11",
        conf_file="/tmp/none.conf",
        standalone_mode=False,
        extra_shell_script="",
        mounted_cache="/mnt/cache",
        mounted_pip_dl="/mnt/pip_dl",
        mounted_vcs_cache="/mnt/vcs",
        mounted_venvs_cache="/mnt/venvs",
    )
    worker = _bound(
        SimpleNamespace(
            debug=lambda *args, **kwargs: None,
            log=mock.MagicMock(),
            _bootstrap=False,
            _services_mode=False,
            _server_is_loopback=lambda: False,
            _worker_label="cairnml-worker={}",
            _parent_worker_label="cairnml-parent-worker={}",
            _session=SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: default)),
            _use_podman=False,
        )
    )
    command = worker_module.Worker._get_docker_cmd(worker, worker_module.DockerRun(**arguments))
    assert command == [
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
        "-v",
        "/tmp/none.conf:/tmp/cairnml.conf",
        "-e",
        "CAIRNML_CONFIG_FILE=/tmp/cairnml.conf",
        "--rm",
        "img:1",
        "bash",
        "-c",
        'echo \'Binary::apt::APT::Keep-Downloaded-Packages "true";\' > /etc/apt/apt.conf.d/docker-clean ; chown -R $(whoami) $HOME/.cache/pip ; export DEBIAN_FRONTEND=noninteractive ; export CAIRNML_APT_INSTALL="$CAIRNML_APT_INSTALL" ; [ ! -z $(which git 2> /dev/null || command -v git) ] || export CAIRNML_APT_INSTALL="$CAIRNML_APT_INSTALL git" ; declare LOCAL_PYTHON ; [ ! -z $LOCAL_PYTHON ] || for i in {20..5}; do (which 3.$i 2> /dev/null || command -v 3.$i) && 3.$i -m pip --version && export LOCAL_PYTHON=$(which 3.$i 2> /dev/null || command -v 3.$i) && break ; done ; [ ! -z $LOCAL_PYTHON ] || export CAIRNML_APT_INSTALL="$CAIRNML_APT_INSTALL 3-pip" ; [ -z "$CAIRNML_APT_INSTALL" ] || (apt-get update -y ; apt-get install -y $CAIRNML_APT_INSTALL) || (dnf install -y $CAIRNML_APT_INSTALL) ; rm -f /usr/lib/python3.*/EXTERNALLY-MANAGED ; [ ! -z $LOCAL_PYTHON ] || export LOCAL_PYTHON=3.11 ; $LOCAL_PYTHON -m pip --version > /dev/null || export LOCAL_PYTHON=$(PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin command -v python3) ; $LOCAL_PYTHON -m pip install -U "pip" ; $LOCAL_PYTHON -m pip install -U "$CAIRNML_AGENT_LOCAL_SDK_WHEEL" ; $LOCAL_PYTHON -m pip install -U "/tmp/agent.whl" ; cp /tmp/cairnml.conf ~/default_cairnml.conf ; NVIDIA_VISIBLE_DEVICES=all $LOCAL_PYTHON -u -m cairnml_agent ',
    ]

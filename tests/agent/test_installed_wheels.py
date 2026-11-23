"""The job containers' wheels come out of an installed agent and SDK (issue #145), checked in clean virtual environments.

The test is the one CI can run: the SDK wheel is built here from the SDK's source tree with pip, a helper that does not
use the code under test. The agent and the SDK are installed in a clean venv, that venv builds the two wheels the job
containers get, and a second clean venv installs those and starts the agent. Before the fix the first venv fails with
"is not a source tree of cairnml-agent".
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cairnml_agent.commands import worker

AGENT_ROOT = Path(__file__).resolve().parents[1]
IGNORED = shutil.ignore_patterns(".git", "build", "dist", "*.egg-info", "__pycache__")


def sdk_source_tree() -> Path:
    """The SDK's source tree: ``CAIRNML_SDK_TREE``, the ``sdk`` checkout of CI, or the sibling ``cairnml``."""
    candidates = [Path(os.environ["CAIRNML_SDK_TREE"])] if os.environ.get("CAIRNML_SDK_TREE") else []
    for candidate in [*candidates, AGENT_ROOT / "sdk", AGENT_ROOT.parent / "cairnml"]:
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file() and re.search(r'^name\s*=\s*"cairnml"\s*$', pyproject.read_text(), re.M):
            return candidate
    if os.environ.get("CI"):
        pytest.fail("no CairnML SDK source tree next to the agent in CI (expected the sdk checkout)")
    pytest.skip("no CairnML SDK source tree next to the agent")


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, timeout=900, **kwargs)


def pip_install(venv: Path, *packages):
    done = run([str(venv / "bin" / "python"), "-m", "pip", "install", "-q", *map(str, packages)])
    assert done.returncode == 0, done.stderr[-2000:]


def test_the_job_wheels_of_an_installed_agent_and_sdk_install_and_run(tmp_path):
    # the SDK wheel, built independently of the code under test: a copy of the tree, built with pip
    sdk_copy = tmp_path / "sdk-src"
    shutil.copytree(sdk_source_tree(), sdk_copy, ignore=IGNORED)
    sdk_out = tmp_path / "sdk-wheel"
    done = run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(sdk_out), str(sdk_copy)])
    assert done.returncode == 0, done.stderr[-2000:]
    [sdk_wheel] = sdk_out.glob("cairnml-*.whl")
    agent_wheel = worker.build_local_wheel(AGENT_ROOT, "cairnml-agent")

    # a clean venv with the two installed, as a pip install leaves them
    install_venv = tmp_path / "install-venv"
    done = run([sys.executable, "-m", "venv", str(install_venv)])
    assert done.returncode == 0, done.stderr[-2000:]
    pip_install(install_venv, sdk_wheel, agent_wheel)

    # from that venv, isolated and outside the source tree: what the agent hands to a job container
    script = (
        "import importlib.metadata as m, json\n"
        "from types import SimpleNamespace\n"
        "from cairnml_agent.commands import worker\n"
        "run = SimpleNamespace()\n"
        "args = []\n"
        "worker.Worker._docker_wheel_args(SimpleNamespace(_bootstrap=False), run, args)\n"
        "print(json.dumps({\n"
        "    'agent_version': m.version('cairnml-agent'), 'sdk_version': m.version('cairnml'),\n"
        "    'agent_wheel': worker.local_agent_wheel(), 'sdk_wheel': worker.local_sdk_wheel(), 'args': args,\n"
        "    'run_agent_wheel': run.cairnml_agent_wheel,\n"
        "}))\n"
    )
    done = run([str(install_venv / "bin" / "python"), "-I", "-c", script], cwd=str(tmp_path))
    assert done.returncode == 0, done.stderr[-2000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])

    agent_name = "cairnml_agent-{}-py3-none-any.whl".format(found["agent_version"])
    sdk_name = "cairnml-{}-py3-none-any.whl".format(found["sdk_version"])
    assert Path(found["agent_wheel"]).name == agent_name
    assert Path(found["sdk_wheel"]).name == sdk_name
    args = found["args"]
    assert args[args.index("-v") + 1] == "{}:/tmp/{}".format(found["agent_wheel"], agent_name)
    assert "{}:/tmp/{}".format(found["sdk_wheel"], sdk_name) in args
    assert "CAIRNML_AGENT_LOCAL_SDK_WHEEL=/tmp/{}".format(sdk_name) in args
    assert found["run_agent_wheel"] == '"/tmp/{}"'.format(agent_name)

    # a second clean venv installs the job wheels, as the job container does, and starts the agent
    job_venv = tmp_path / "job-venv"
    done = run([sys.executable, "-m", "venv", str(job_venv)])
    assert done.returncode == 0, done.stderr[-2000:]
    pip_install(job_venv, found["sdk_wheel"], found["agent_wheel"])
    done = run([str(job_venv / "bin" / "cairnml-agent"), "--help"], cwd=str(tmp_path))
    assert done.returncode == 0, done.stderr[-2000:]

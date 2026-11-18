"""The agent against a real server (cairnml-agent issue #112): enrolment, the worker, and a job that completes.

The checks are one chain, as in the SDK's tests/integration. The agent is enrolled first, its daemon registers as a
worker on the queue that enrolment made for it, and then a job runs on it: the job's repository is served over git://
on the loopback address, the agent pulls it, runs the script in a virtual environment (--allow-venv) that installs the
SDK from the working tree, and the task ends ``completed`` with the parameters and the scalar the script recorded.

The client side (creating and enqueueing the task, reading it back) uses the SDK in a separate process whose HOME is an
enrolled client home, so the test process itself never imports the SDK.
"""

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from .device_signin import enrol, machine_environment

pytestmark = pytest.mark.integration

# A job is a virtual environment built from the package index, then the script: minutes on a warm cache, not seconds.
JOB_TIMEOUT_SECONDS = 600
FINAL_STATUSES = ("completed", "failed", "stopped", "aborted", "closed")
EPOCHS = 3
LEARNING_RATE = "0.1"

# The job's script: the parameters come from the task, and the loss is the scalar the server keeps for the run.
TRAIN_SCRIPT = """\
from cairnml import Task

task = Task.init(auto_resource_monitoring=False)
parameters = task.connect({"epochs": 1, "lr": 0.5})
logger = task.get_logger()
for epoch in range(int(parameters["epochs"])):
    logger.report_scalar("loss", "train", value=1.0 / (epoch + 1), iteration=epoch)
logger.flush()
print(f"job finished: epochs = {parameters['epochs']}")
"""

CREATE_AND_QUEUE = """\
import json, sys
from pathlib import Path

import cairnml
from cairnml import Task

project, name, repo, queue = sys.argv[1:5]
# the SDK of this environment, from its source tree: the job installs that tree, never a released package
sdk_source = Path(cairnml.__file__).resolve().parents[1]
task = Task.create(
    project_name=project,
    task_name=name,
    repo=repo,
    branch="main",
    script="train.py",
    working_directory=".",
    packages=[f"cairnml @ {sdk_source.as_uri()}"],
    add_task_init_call=False,
    detect_repository=False,
)
task.set_parameters_as_dict({"General": {"epochs": EPOCHS, "lr": LR}})
Task.enqueue(task.id, queue_name=queue)
print(json.dumps({"task_id": task.id}))
"""

WORKERS = """\
import json
from cairnml.backend_api import Session
from cairnml.backend_api.services import workers

result = Session().send(workers.GetAllRequest())
print(json.dumps([worker.id for worker in result.response.workers or []]))
"""

WATCH = """\
import json, sys, time
from cairnml import Task

task_id, timeout = sys.argv[1], float(sys.argv[2])
deadline = time.monotonic() + timeout
status = None
while time.monotonic() < deadline:
    status = Task.get_task(task_id=task_id).get_status()
    if status in FINAL:
        break
    time.sleep(3)
task = Task.get_task(task_id=task_id)
scalars = task.get_reported_scalars()
print(json.dumps({
    "status": status,
    "parameters": task.get_parameters_as_dict(),
    "loss": scalars.get("loss", {}).get("train", {}).get("y", []),
}))
"""


def binary(name: str) -> str:
    """A command of the environment the tests run in: the agent under test is the one installed beside the tests."""
    return str(Path(sys.executable).parent / name)


def run_as(home: Path, code: str, *args, timeout: float = 300) -> dict:
    """Runs SDK code as the enrolled client in home and returns the JSON document its last line prints."""
    template = _with_constants(code)
    completed = subprocess.run(
        [sys.executable, "-c", template, *map(str, args)],
        env=machine_environment(home),
        cwd=home,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise AssertionError(f"the client call failed with status {completed.returncode}:\n{completed.stderr[-3000:]}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _with_constants(code: str) -> str:
    header = f"FINAL = {FINAL_STATUSES!r}\nEPOCHS = {EPOCHS}\nLR = {LEARNING_RATE!r}\n"
    return header + code


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _accepts(port: int) -> bool:
    with socket.socket() as attempt:
        return attempt.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="module")
def chain(tmp_path_factory) -> dict:
    """What each check leaves for the checks after it, and the scratch area they share."""
    return {"workspace": tmp_path_factory.mktemp("agent-integration"), "project": f"agent-it-{uuid.uuid4().hex[:8]}"}


def need(chain: dict, key: str):
    if key not in chain:
        pytest.fail(f"needs {key!r}, which an earlier check should have left: that check failed", pytrace=False)
    return chain[key]


@pytest.fixture(scope="module")
def client_home(real_server, chain) -> Path:
    """An enrolled client home: the SDK that creates and reads the tasks of the chain."""
    home = need(chain, "workspace") / "client-home"
    name = f"agent-it-client-{uuid.uuid4().hex[:6]}"
    enrol(binary("cairnml"), home, real_server["server"], name, real_server["user"], real_server["password"])
    return home


@pytest.fixture(scope="module")
def job_repository(chain):
    """A git repository with the job's script, served over git:// on the loopback address (yields its clone address)."""
    root = need(chain, "workspace") / "git"
    seed = root / "seed"
    remote = root / "job.git"
    seed.mkdir(parents=True)
    identity = ["-c", "user.name=Integration", "-c", "user.email=integration@example.test"]
    git = ["git", *identity]
    subprocess.run([*git, "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "train.py").write_text(TRAIN_SCRIPT)
    subprocess.run([*git, "-C", str(seed), "add", "train.py"], check=True)
    subprocess.run([*git, "-C", str(seed), "commit", "-q", "-m", "job"], check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run([*git, "-C", str(seed), "push", "-q", str(remote), "main"], check=True)

    port = free_port()
    daemon = subprocess.Popen(
        ["git", "daemon", "--export-all", f"--base-path={root.as_posix()}", "--listen=127.0.0.1", f"--port={port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not _accepts(port):
            if daemon.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"git daemon did not start: {daemon.stderr.read()}")
            time.sleep(0.1)
        yield f"git://127.0.0.1:{port}/job.git"
    finally:
        daemon.terminate()
        daemon.wait(timeout=10)


@pytest.fixture(scope="module")
def agent_home(real_server, chain) -> Path:
    """An agent enrolled as a machine of its own: its device key and the queue enrolment made for it."""
    home = need(chain, "workspace") / "agent-home"
    name = f"agent-it-{uuid.uuid4().hex[:6]}"
    output = enrol(
        binary("cairnml-agent"), home, real_server["server"], name, real_server["user"], real_server["password"]
    )
    match = re.search(r"queue of this machine: (\S+)", output)
    if not match:
        pytest.fail(f"enrolment named no queue:\n{output}", pytrace=False)
    chain["agent_name"] = name
    chain["queue"] = match.group(1)
    return home


@pytest.fixture(scope="module")
def daemon(agent_home, chain):
    """The agent's daemon on its own queue, with jobs in a virtual environment on this host (--allow-venv)."""
    env = machine_environment(agent_home)
    env["CAIRNML_WORKER_NAME"] = need(chain, "agent_name")
    log_path = need(chain, "workspace") / "daemon.log"
    with open(log_path, "wb") as log:
        process = subprocess.Popen(
            [
                binary("cairnml-agent"),
                "daemon",
                "--queue",
                need(chain, "queue"),
                "--allow-venv",
                "--polling-interval",
                "2",
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    chain["daemon_log"] = log_path
    try:
        yield process
    finally:
        if process.poll() is None:
            # the daemon runs in its own session: its whole group is stopped, by the process id this test started
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)


def test_01_the_agent_enrols_and_its_doctor_is_green(real_server, chain, agent_home):
    assert (agent_home / ".cairnml" / "agent" / "device.key").is_file()
    doctor = subprocess.run(
        [binary("cairnml-agent"), "doctor"],
        env=machine_environment(agent_home),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr


def test_02_the_daemon_registers_as_a_worker_on_its_queue(real_server, chain, client_home, daemon):
    worker = need(chain, "agent_name")
    log_path = Path(need(chain, "daemon_log"))
    deadline = time.monotonic() + 120
    listed = []
    while time.monotonic() < deadline:
        if daemon.poll() is not None:
            log = log_path.read_text(errors="replace")[-3000:]
            pytest.fail(f"the daemon exited with status {daemon.returncode}:\n{log}", pytrace=False)
        listed = run_as(client_home, WORKERS)
        if any(item == worker or item.startswith(worker + ":") for item in listed):
            break
        time.sleep(3)
    else:
        pytest.fail(f"no worker named {worker!r} after 120 s: the server lists {listed}", pytrace=False)
    log = log_path.read_text(errors="replace")
    assert "Listening to queues" in log and need(chain, "queue") in log, log[-3000:]


def test_03_a_job_from_a_git_repository_completes(real_server, chain, client_home, job_repository, daemon):
    queue = need(chain, "queue")
    started = time.monotonic()
    created = run_as(
        client_home, CREATE_AND_QUEUE, need(chain, "project"), "agent integration job", job_repository, queue
    )
    task_id = created["task_id"]
    result = run_as(client_home, WATCH, task_id, JOB_TIMEOUT_SECONDS, timeout=JOB_TIMEOUT_SECONDS + 120)
    elapsed = time.monotonic() - started
    print(f"\njob {task_id} ended {result['status']!r} after {elapsed:.0f} s")
    assert daemon.poll() is None, Path(need(chain, "daemon_log")).read_text(errors="replace")[-3000:]
    assert result["status"] == "completed", (
        f"status {result['status']!r}\n" + Path(need(chain, "daemon_log")).read_text(errors="replace")[-3000:]
    )
    parameters = result["parameters"]["General"]
    assert str(parameters["epochs"]) == str(EPOCHS), parameters
    assert str(parameters["lr"]) == LEARNING_RATE, parameters
    assert len(result["loss"]) == EPOCHS, result["loss"]
    assert result["loss"][-1] == pytest.approx(1.0 / EPOCHS), result["loss"]

"""Real containers with the limits of a reservation. Needs docker and the python:3.11-slim image.
Skipped where docker is not usable."""

import shutil
import subprocess
import sys
import time

import pytest

from clearml_agent.scheduler import enforce
from clearml_agent.scheduler.runner import JobRunner

IMAGE = "python:3.11-slim"


def docker_ready():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not docker_ready(), reason="docker or the test image is not available")


def container_builder(code):
    def builder(task, reservation):
        argv = ["docker", "run", *enforce.docker_limit_args(reservation, task),
                "--label", f"{enforce.JOB_LABEL}={task}", IMAGE, "python3", "-c", code]
        return argv, {}
    return builder


def wait_for(runner, count=1, timeout=180):
    results, deadline = [], time.time() + timeout
    while len(results) < count and time.time() < deadline:
        results.extend(runner.finished())
        time.sleep(0.5)
    return results


def test_an_allocation_over_the_memory_limit_is_reported_as_out_of_memory(tmp_path):
    reservation = {"cpus": [0], "gpus": [], "ram_gb": 0.25, "disk_gb": 1, "exclusive": False}
    code = "b = bytearray(400 * 1024 * 1024); print('allocated')"
    runner = JobRunner(command_builder=container_builder(code), log_dir=str(tmp_path))
    runner.start("real-oom", reservation)
    (result,) = wait_for(runner)
    assert result["outcome"] == "failed", result
    assert result["failure"]["reason"] == "oom", result["failure"]
    assert result["failure"]["reserved"] == {"ram_gb": 0.25}
    assert result["failure"]["detail"].startswith("Failure due to bad compute")


def test_the_job_sees_only_its_cpus(tmp_path):
    reservation = {"cpus": [2, 3], "gpus": [], "ram_gb": 1, "disk_gb": 1, "exclusive": False}
    code = "import os; print('cpus', sorted(os.sched_getaffinity(0)))"
    runner = JobRunner(command_builder=container_builder(code), log_dir=str(tmp_path))
    runner.start("real-cpus", reservation)
    (result,) = wait_for(runner)
    assert result["outcome"] == "completed", result
    log = open(f"{tmp_path}/real-cpus.log").read()
    assert "cpus [2, 3]" in log


def test_two_jobs_run_side_by_side_on_disjoint_cpus(tmp_path):
    code = "import time, os; print('cpus', sorted(os.sched_getaffinity(0))); time.sleep(3)"
    runner = JobRunner(command_builder=container_builder(code), log_dir=str(tmp_path))
    runner.start("pair-a", {"cpus": [4, 5], "gpus": [], "ram_gb": 1, "disk_gb": 1, "exclusive": False})
    runner.start("pair-b", {"cpus": [6, 7], "gpus": [], "ram_gb": 1, "disk_gb": 1, "exclusive": False})
    time.sleep(1.5)
    assert set(runner.running_tasks()) == {"pair-a", "pair-b"}
    results = wait_for(runner, count=2)
    assert sorted(r["outcome"] for r in results) == ["completed", "completed"]
    assert "cpus [4, 5]" in open(f"{tmp_path}/pair-a.log").read()
    assert "cpus [6, 7]" in open(f"{tmp_path}/pair-b.log").read()


def test_the_time_limit_kills_the_container_and_reports_time_limit(tmp_path):
    reservation = {"cpus": [0], "gpus": [], "ram_gb": 1, "disk_gb": 1, "exclusive": False}
    runner = JobRunner(command_builder=container_builder("import time; time.sleep(120)"), log_dir=str(tmp_path))
    runner.start("real-slow", reservation, max_runtime_min=0.05)
    (result,) = wait_for(runner)
    assert result["outcome"] == "failed", result
    assert result["failure"]["reason"] == "time_limit"
    assert subprocess.run(["docker", "ps", "-aq", "--filter", "label=clearml.job=real-slow"],
                          capture_output=True, text=True).stdout.strip() == ""

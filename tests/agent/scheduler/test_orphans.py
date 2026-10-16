import shutil
import subprocess

import pytest

from clearml_agent.scheduler import enforce
from clearml_agent.scheduler.runner import JobRunner

pytestmark = pytest.mark.skipif(not shutil.which("docker"), reason="docker is not available")


def test_containers_left_by_an_earlier_daemon_are_stopped(tmp_path):
    # a job container that outlived its daemon
    cid = subprocess.run(
        ["docker", "run", "-d", "--label", f"{enforce.JOB_LABEL}=orphan-task", "python:3.11-slim",
         "sleep", "300"], capture_output=True, text=True, check=True).stdout.strip()
    try:
        stopped = JobRunner(log_dir=str(tmp_path)).stop_orphans()
        assert cid[:12] in [s[:12] for s in stopped]
        assert subprocess.run(["docker", "ps", "-aq", "--filter", f"id={cid}"],
                              capture_output=True, text=True).stdout.strip() == ""
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)

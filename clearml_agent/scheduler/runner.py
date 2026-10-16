"""Runs one job per claimed offer: a `clearml-agent execute` process whose container carries the limits
and a label with the task id. It watches the job's container, samples its usage, enforces the time limit,
and reports how the job ended. Each job has its own process, so jobs share no state."""

import os
import sys
import shutil
import subprocess
import tempfile
import threading
from typing import Callable, Dict, List, Optional, Tuple

from clearml_agent.scheduler import enforce, failure, usage

CommandBuilder = Callable[[str, dict], Tuple[List[str], dict]]


def default_command(task: str, reservation: dict) -> Tuple[List[str], dict]:
    # the same interpreter as the daemon, so the job runs the agent of this virtualenv
    return [sys.executable, "-m", "clearml_agent", "execute", "--id", task, "--docker"], {}


class JobRunner(object):
    def __init__(self, command_builder: CommandBuilder = default_command, log_dir: Optional[str] = None,
                 docker: str = "docker", expect_container: bool = True):
        self._build = command_builder
        self._expect_container = expect_container
        self._log_dir = log_dir or tempfile.mkdtemp(prefix="clearml-jobs-")
        self._docker = docker
        self._jobs: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def running_tasks(self) -> List[str]:
        with self._lock:
            return list(self._jobs)

    def start(self, task: str, reservation: dict, max_runtime_min: Optional[int] = None):
        # The job's own process requests its job token itself, with its device session (as the agent does).
        argv, extra_env = self._build(task, reservation)
        env = {**os.environ, **enforce.job_env(reservation, task), **extra_env}
        log_path = os.path.join(self._log_dir, f"{task}.log")
        log = open(log_path, "wb")
        proc = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        job = {"proc": proc, "log": log, "log_path": log_path, "reservation": reservation,
               "deadline_hit": False, "stop_requested": False, "peak_ram_gb": None, "max_runtime_min": max_runtime_min,
               "timer": None}
        if max_runtime_min:
            job["timer"] = threading.Timer(max_runtime_min * 60, self._deadline, args=(task,))
            job["timer"].daemon = True
            job["timer"].start()
        with self._lock:
            self._jobs[task] = job

    def _deadline(self, task: str):
        with self._lock:
            job = self._jobs.get(task)
            if job:
                job["deadline_hit"] = True
        self.kill(task)

    def stop(self, task: str):
        """Asked by the server: the task was stopped. The job is killed and reported as stopped."""
        with self._lock:
            job = self._jobs.get(task)
            if job:
                job["stop_requested"] = True
        self.kill(task)

    def kill(self, task: str):
        for cid in self._containers(task):
            subprocess.run([self._docker, "kill", cid], capture_output=True, timeout=60)
        with self._lock:
            job = self._jobs.get(task)
        if job and job["proc"].poll() is None:
            try:
                os.killpg(job["proc"].pid, 15)
            except OSError:
                pass

    def sample(self):
        """Read every running job's usage. The peak is kept, because the cgroup is gone when the job ends."""
        with self._lock:
            tasks = list(self._jobs)
        for task in tasks:
            for cid in self._containers(task):
                sample = usage.read_container_usage(cid)
                if sample:
                    with self._lock:
                        job = self._jobs.get(task)
                        if job:
                            peak = sample.get("peak_ram_gb") or sample["ram_gb"]
                            job["peak_ram_gb"] = max(job["peak_ram_gb"] or 0, peak)
                            job["usage"] = sample

    def usage_of(self, task: str) -> dict:
        with self._lock:
            job = self._jobs.get(task)
        if not job or not job.get("usage"):
            return {}
        return {"ram_gb": job["usage"]["ram_gb"], "cpu_seconds": job["usage"]["cpu_seconds"],
                "peak_ram_gb": job["peak_ram_gb"]}

    def finished(self) -> List[dict]:
        """Jobs whose process has ended. Each result has the outcome and, for a failure, its record."""
        results = []
        with self._lock:
            done = [t for t, j in self._jobs.items() if j["proc"].poll() is not None]
        for task in done:
            with self._lock:
                job = self._jobs.pop(task)
            if job["timer"]:
                job["timer"].cancel()
            job["log"].close()
            results.append(self._result(task, job))
        return results

    def _result(self, task: str, job: dict) -> dict:
        exit_code = job["proc"].returncode
        inspect = None
        for cid in self._containers(task, include_stopped=True):
            inspect = self._inspect(cid)
            subprocess.run([self._docker, "rm", "-f", cid], capture_output=True, timeout=60)
            break
        with open(job["log_path"], "r", encoding="utf-8", errors="replace") as h:
            log_tail = "\n".join(h.read().splitlines()[-200:])
        reservation = job["reservation"]
        reserved = {"ram_gb": reservation.get("ram_gb")}
        peak = {"ram_gb": job["peak_ram_gb"]}
        if job["stop_requested"] and not job["deadline_hit"]:
            return {"task": task, "outcome": "stopped", "failure": None}
        if self._expect_container and inspect is None and not job["deadline_hit"]:
            # the process ended without a container: the job never ran, whatever its exit code says
            return {"task": task, "outcome": "failed", "log_tail": log_tail,
                    "failure": {"reason": "error", "exit_code": exit_code,
                                "detail": "The job did not start: no container was created. Last lines:\n"
                                          + "\n".join(log_tail.splitlines()[-8:])}}
        record = failure.classify(exit_code, inspect, log_tail, reserved, peak,
                                  deadline_minutes=job["max_runtime_min"], deadline_hit=job["deadline_hit"])
        if record is None:
            return {"task": task, "outcome": "completed", "failure": None}
        return {"task": task, "outcome": "failed", "failure": record, "log_tail": log_tail}

    def _containers(self, task: str, include_stopped: bool = False) -> List[str]:
        cmd = [self._docker, "ps", "-q", "--filter", f"label={enforce.JOB_LABEL}={task}"]
        if include_stopped:
            cmd.insert(2, "-a")
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return [c for c in out.stdout.split() if c]

    def _inspect(self, cid: str) -> Optional[dict]:
        out = subprocess.run([self._docker, "inspect", cid], capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return None
        import json
        data = json.loads(out.stdout)
        return data[0] if data else None

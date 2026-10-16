"""Turns a reservation into the limits of one job's container.

The limits are passed to `clearml-agent execute` through its environment (CLEARML_AGENT_EXTRA_DOCKER_ARGS
and CLEARML_AGENT_EXTRA_DOCKER_LABELS), so no value is parsed as an agent option."""

import shlex
from typing import Iterable, List

JOB_LABEL = "clearml.job"


def compact_ranges(ids: Iterable[int]) -> str:
    """[0, 1, 2, 5] -> "0-2,5"; the form docker expects for --cpuset-cpus."""
    values = sorted(set(int(i) for i in ids))
    if not values:
        return ""
    parts = []
    start = prev = values[0]
    for v in values[1:]:
        if v == prev + 1:
            prev = v
            continue
        parts.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = v
    parts.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ",".join(parts)


def memory_arg(ram_gb: float) -> str:
    """Whole megabytes, so the limit is exact and docker accepts it."""
    return f"{max(1, int(round(float(ram_gb) * 1024)))}m"


def docker_limit_args(reservation: dict, task_id: str, pids_limit: int = 1024) -> List[str]:
    """The docker run arguments that hold a job to its reservation. Exclusive jobs get no CPU or memory
    limit beyond the machine itself, because they are alone on it."""
    args: List[str] = []
    cpus = compact_ranges(reservation.get("cpus") or [])
    if cpus:
        args += ["--cpuset-cpus", cpus]
    if reservation.get("ram_gb") and not reservation.get("exclusive"):
        mem = memory_arg(reservation["ram_gb"])
        args += ["--memory", mem, "--memory-swap", mem]
    args += ["--pids-limit", str(pids_limit)]
    return args


def job_env(reservation: dict, task_id: str) -> dict:
    """Environment for `clearml-agent execute`: the limits, the label that finds the container later, and
    the GPUs the job may see (none when it asked for none, so the agent does not add --gpus all)."""
    gpus = reservation.get("gpus") or []
    return {
        "CLEARML_AGENT_EXTRA_DOCKER_ARGS": " ".join(shlex.quote(a) for a in docker_limit_args(reservation, task_id)),
        "CLEARML_AGENT_EXTRA_DOCKER_LABELS": f"{JOB_LABEL}={task_id}",
        "NVIDIA_VISIBLE_DEVICES": ",".join(str(g) for g in gpus) if gpus else "none",
    }

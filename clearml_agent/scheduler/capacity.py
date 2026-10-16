"""What the machine has, and what is kept back for the host. Sent to the server once at registration."""

import math
import os
import re
import shutil
import subprocess
from typing import List, Optional

DEFAULT_RESERVE = {"cpus": 1, "ram_percent": 10, "disk_percent": 10}


def parse_nvidia_smi(csv_text: str) -> List[dict]:
    """Lines of `nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader,nounits`."""
    gpus = []
    for line in csv_text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        gpus.append({"index": int(parts[0]), "model": parts[1], "memory_gb": round(float(parts[2]) / 1024, 2)})
    return gpus


def detect_gpus() -> List[dict]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_nvidia_smi(out.stdout) if out.returncode == 0 else []


def _ram_total_gb(meminfo: str) -> float:
    m = re.search(r"MemTotal:\s+(\d+) kB", meminfo)
    return int(m.group(1)) * 1024 / 2**30 if m else 0.0


def detect(workdir: str, meminfo: str, gpus: List[dict], cpus: Optional[int] = None) -> dict:
    return {
        "cpus": cpus or len(os.sched_getaffinity(0)),
        "ram_gb": round(_ram_total_gb(meminfo), 2),
        "disk_gb": round(shutil.disk_usage(workdir).total / 1e9, 2),
        "gpus": gpus,
    }


def reserve_for(capacity: dict, configured: Optional[dict] = None) -> dict:
    """The part kept for the operating system and the host services. Defaults: 1 CPU, 10 % of RAM and disk."""
    cfg = {**DEFAULT_RESERVE, **(configured or {})}
    return {
        "cpus": min(int(cfg["cpus"]), max(0, capacity["cpus"] - 1)),
        "ram_gb": round(math.ceil(capacity["ram_gb"] * float(cfg["ram_percent"]) / 100), 2),
        "disk_gb": round(capacity["disk_gb"] * float(cfg["disk_percent"]) / 100, 2),
    }


def can_enforce(docker_ok: bool, has_gpus: bool, nvidia_runtime: bool) -> dict:
    """What this agent can hold a job to. Shared jobs need docker; GPU limits need the NVIDIA runtime."""
    return {
        "docker": bool(docker_ok),
        "cpuset": bool(docker_ok),
        "gpu_device_limit": bool(docker_ok and has_gpus and nvidia_runtime),
    }


def docker_info_ok() -> (bool, bool):
    """(docker works, nvidia runtime registered)."""
    try:
        out = subprocess.run(["docker", "info", "--format", "{{json .Runtimes}}"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False, False
    return out.returncode == 0, "nvidia" in out.stdout

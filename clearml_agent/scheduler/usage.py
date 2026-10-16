"""Usage of a job's container and of the machine, read from the kernel (cgroup v2 or v1) with docker
stats as the fallback. Parsers take text so they can be tested without a machine."""

import os
import re
import shutil
from typing import Optional

CGROUP_ROOT = "/sys/fs/cgroup"


def container_cgroup_dirs(container_id: str, root: str = CGROUP_ROOT):
    return [
        os.path.join(root, "system.slice", f"docker-{container_id}.scope"),
        os.path.join(root, "docker", container_id),
    ]


def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as h:
            return h.read()
    except OSError:
        return None


def read_container_usage(container_id: str, root: str = CGROUP_ROOT) -> Optional[dict]:
    """{cpu_seconds, ram_gb, peak_ram_gb} from the container's cgroup, or None when it is not there."""
    for d in container_cgroup_dirs(container_id, root):
        current = _read(os.path.join(d, "memory.current"))
        if current is None:
            continue
        peak = _read(os.path.join(d, "memory.peak"))
        cpu = _read(os.path.join(d, "cpu.stat"))
        return {
            "ram_gb": int(current.strip()) / 2**30,
            "peak_ram_gb": (int(peak.strip()) / 2**30) if peak and peak.strip().isdigit() else None,
            "cpu_seconds": parse_cpu_usage_usec(cpu or "") / 1e6,
        }
    return None


def parse_cpu_usage_usec(cpu_stat: str) -> int:
    for line in cpu_stat.splitlines():
        if line.startswith("usage_usec"):
            return int(line.split()[1])
    return 0


_DOCKER_MEM = re.compile(r"([\d.]+)\s*([KMGT]i?B)\s*/", re.IGNORECASE)
_UNITS = {"B": 1, "KB": 1e3, "KIB": 2**10, "MB": 1e6, "MIB": 2**20, "GB": 1e9, "GIB": 2**30, "TB": 1e12, "TIB": 2**40}


def parse_docker_stats(line: str) -> Optional[dict]:
    """`docker stats --no-stream --format '{{.CPUPerc}} {{.MemUsage}}'`, e.g. '12.50% 1.2GiB / 62GiB'."""
    m_cpu = re.match(r"\s*([\d.]+)%", line)
    m_mem = _DOCKER_MEM.search(line)
    if not m_cpu or not m_mem:
        return None
    ram_bytes = float(m_mem.group(1)) * _UNITS[m_mem.group(2).upper()]
    return {"cpu_percent": float(m_cpu.group(1)), "ram_gb": ram_bytes / 1e9}


def parse_meminfo(text: str) -> dict:
    values = {}
    for line in text.splitlines():
        m = re.match(r"(\w+):\s+(\d+)", line)
        if m:
            values[m.group(1)] = int(m.group(2)) * 1024
    total = values.get("MemTotal", 0)
    avail = values.get("MemAvailable", 0)
    return {"ram_total_gb": total / 2**30, "ram_used_gb": (total - avail) / 2**30}


def parse_cpu_times(stat_text: str):
    """(idle, total) jiffies from the first line of /proc/stat."""
    first = stat_text.splitlines()[0].split()
    values = [int(v) for v in first[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


def cpu_percent(before, after) -> float:
    idle_b, total_b = before
    idle_a, total_a = after
    total = total_a - total_b
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1 - (idle_a - idle_b) / total)))


def disk_used_percent(path: str) -> float:
    usage = shutil.disk_usage(path)
    return 100.0 * usage.used / usage.total if usage.total else 0.0

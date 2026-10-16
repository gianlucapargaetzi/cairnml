"""Why a job ended badly. The first strong signal wins: the kernel's out-of-memory kill, then the time
limit, then a GPU or disk error in the log, then the exit code. A job that exits 0 has no failure."""

import re
from typing import Optional

GPU_OOM = re.compile(r"CUDA out of memory|out of memory.*GPU|CUBLAS_STATUS_ALLOC_FAILED", re.IGNORECASE)
GPU_ERROR = re.compile(r"CUDA error|Xid \d+", re.IGNORECASE)
DISK_FULL = re.compile(r"No space left on device", re.IGNORECASE)


def _tail(log_tail: str, lines: int = 20) -> str:
    return "\n".join((log_tail or "").splitlines()[-lines:])


def classify(
    exit_code: int,
    inspect: Optional[dict],
    log_tail: str,
    reserved: dict,
    peak: dict,
    deadline_minutes: Optional[int] = None,
    deadline_hit: bool = False,
) -> Optional[dict]:
    """Return the failure record for the server, or None when the job succeeded."""
    state = (inspect or {}).get("State") or {}
    if deadline_hit:
        return {
            "reason": "time_limit",
            "detail": f"Failure due to time limit: stopped after {deadline_minutes} minutes (max_runtime_min)",
            "exit_code": exit_code,
        }
    if state.get("OOMKilled"):
        limit = reserved.get("ram_gb")
        peak_ram = peak.get("ram_gb")
        detail = "Failure due to bad compute: out of memory, killed by the kernel"
        if limit is not None:
            detail += f" at the {limit:g} GB limit"
        if peak_ram is not None:
            detail += f" (peak {peak_ram:.1f} GB)"
        return {"reason": "oom", "detail": detail, "exit_code": exit_code,
                "reserved": {"ram_gb": limit}, "peak": {"ram_gb": peak_ram}}
    if exit_code == 0:
        return None
    if GPU_OOM.search(log_tail or ""):
        return {"reason": "gpu_error",
                "detail": "Failure due to bad compute: the GPU ran out of memory",
                "exit_code": exit_code, "reserved": reserved, "peak": peak}
    if DISK_FULL.search(log_tail or ""):
        return {"reason": "disk_full", "detail": "Failure due to disk: no space left on the device",
                "exit_code": exit_code}
    if GPU_ERROR.search(log_tail or ""):
        return {"reason": "gpu_error", "detail": "Failure due to bad compute: a GPU error (see the log)",
                "exit_code": exit_code, "reserved": reserved, "peak": peak}
    return {"reason": "error", "detail": f"Exited with code {exit_code}. Last lines:\n{_tail(log_tail, 8)}",
            "exit_code": exit_code}

"""What a task asks of a machine (resource-aware scheduling). The server checks the same rules; checking
here gives the user the error at the call, before anything is sent."""

from typing import Optional

MAIN_FIELDS = ("cpus", "ram_gb", "gpus", "disk_gb")
DEFAULT_PRIORITY = 50


class RequirementsValueError(ValueError):
    pass


def build_requirements(
    cpus: Optional[int] = None,
    ram_gb: Optional[float] = None,
    gpus: Optional[int] = None,
    gpu_memory_gb: Optional[float] = None,
    gpu_model: Optional[str] = None,
    disk_gb: Optional[float] = None,
    max_runtime_min: Optional[int] = None,
) -> Optional[dict]:
    """The requirements for the server. All of cpus, ram_gb, gpus and disk_gb, or none of them. None means
    the task runs alone on a machine."""
    given = {"cpus": cpus, "ram_gb": ram_gb, "gpus": gpus, "disk_gb": disk_gb}
    if all(v is None for v in given.values()):
        if max_runtime_min is not None:
            raise RequirementsValueError("max_runtime_min needs cpus, ram_gb, gpus and disk_gb as well")
        return None
    missing = [k for k, v in given.items() if v is None]
    if missing:
        raise RequirementsValueError(
            "requirements must name all of cpus, ram_gb, gpus and disk_gb, or none; missing: " + ", ".join(missing)
        )
    if int(cpus) < 1:
        raise RequirementsValueError("cpus must be at least 1")
    if float(ram_gb) <= 0:
        raise RequirementsValueError("ram_gb must be above 0")
    if int(gpus) < 0:
        raise RequirementsValueError("gpus must be 0 or more")
    if float(disk_gb) < 0:
        raise RequirementsValueError("disk_gb must be 0 or more")
    requirements = {
        "cpus": int(cpus),
        "ram_gb": float(ram_gb),
        "gpus": {"count": int(gpus), "min_memory_gb": float(gpu_memory_gb or 0), "model": gpu_model or None},
        "disk_gb": float(disk_gb),
    }
    if max_runtime_min is not None:
        if int(max_runtime_min) < 1:
            raise RequirementsValueError("max_runtime_min must be at least 1")
        requirements["max_runtime_min"] = int(max_runtime_min)
    return requirements


def check_priority(priority: Optional[int]) -> Optional[int]:
    if priority is None:
        return None
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise RequirementsValueError("priority must be an integer")
    if not 0 <= priority <= 100:
        raise RequirementsValueError("priority must be between 0 and 100")
    return priority


def enqueue_body(task_id: str, requirements: Optional[dict], priority: Optional[int]) -> dict:
    """The body of tasks.enqueue at version 2.36."""
    body = {"task": task_id}
    if requirements is not None:
        body["requirements"] = requirements
    if priority is not None:
        body["priority"] = check_priority(priority)
    return body

"""GPU statistics for the resource monitor (and the agent): NVIDIA through the PyPI package gpustat, which reads NVIDIA's
own bindings (nvidia-ml-py, provides the module ``pynvml``). Both come with the extra ``cairnml[gpu]``.

The import fails when the extra is not installed; the resource monitor turns that into a message in the task log.
"""

import atexit
import contextlib
import functools
import io
import os
from typing import Any, Callable, Dict, List, Optional

from gpustat import GPUStat, GPUStatCollection, new_query

_CUDA_VERSION_FUNCTIONS = ("nvmlSystemGetCudaDriverVersion", "nvmlSystemGetCudaDriverVersion_v2")


def query() -> GPUStatCollection:
    """The GPUs of this machine, with their processes. NVIDIA through gpustat; AMD through amdsmi when there is no
    NVIDIA driver. Takes about 100 ms on NVIDIA."""
    try:
        return new_query()
    except Exception:
        if amd_available():
            return _amd_query()
        raise


def driver_cuda_version() -> Optional[str]:
    """The CUDA version the NVIDIA driver supports, as ``major.minor``; None when the driver cannot tell."""
    import pynvml

    # gpustat initialises NVML only for its own query and shuts it down after, so this call does it for itself
    pynvml.nvmlInit()
    try:
        for function_name in _CUDA_VERSION_FUNCTIONS:
            function = getattr(pynvml, function_name, None)
            if function is None:
                continue
            try:
                value = function()
            except Exception:
                continue
            return _format_cuda_version(value)
        return None
    finally:
        pynvml.nvmlShutdown()


def _format_cuda_version(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    try:
        number = int(text)
    except (ValueError, TypeError):
        return text
    return "{}.{}".format(number // 1000, (number % 1000) // 10)


# --- AMD: AMD's own Python API (amdsmi, shipped with the ROCm install, not a pip dependency) -----------------------------
#
# Not run on AMD hardware: the calls follow AMD's documented amdsmi API. Each value that cannot be read is None, and one
# failing call never fails the whole query.

_KFD_DEVICE = "/dev/kfd"  # the ROCm kernel driver; without it there is no AMD GPU to read
_amd_state: Dict[str, Any] = {}


def amd_available() -> bool:
    """True when this host has an AMD GPU driver and amdsmi can be imported and initialised."""
    if not os.path.exists(_KFD_DEVICE):
        return False
    try:
        amdsmi = _import_amdsmi()
    except Exception:
        return False
    return _amd_handles(amdsmi) is not None


def _import_amdsmi() -> Any:
    # the module prints to stdout when its shared library is missing, so the import is kept quiet
    with contextlib.redirect_stdout(io.StringIO()):
        import amdsmi

    return amdsmi


def _amd_handles(amdsmi: Any) -> Optional[List[Any]]:
    if "handles" not in _amd_state:
        try:
            amdsmi.amdsmi_init()
            atexit.register(_amd_shutdown, amdsmi)
            _amd_state["handles"] = list(amdsmi.amdsmi_get_processor_handles())
        except Exception:
            _amd_state["handles"] = None
    return _amd_state["handles"]


def _amd_shutdown(amdsmi: Any) -> None:
    try:
        amdsmi.amdsmi_shut_down()
    except Exception:
        pass


def _read(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except Exception:
        return None


def _number(value: Any) -> Optional[float]:
    if value is None or value == "N/A":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _amd_query() -> GPUStatCollection:
    amdsmi = _import_amdsmi()
    handles = _amd_handles(amdsmi) or []
    gpus = []
    driver_version = None
    for index, handle in enumerate(handles):
        if driver_version is None:
            info = _read(functools.partial(amdsmi.amdsmi_get_gpu_driver_info, handle))
            driver_version = info.get("driver_version") if isinstance(info, dict) else None
        gpus.append(GPUStat(_amd_entry(amdsmi, index, handle)))
    return GPUStatCollection(gpus, driver_version=driver_version)


def _amd_entry(amdsmi: Any, index: int, handle: Any) -> Dict[str, Any]:
    asic = _read(lambda: amdsmi.amdsmi_get_gpu_asic_info(handle)) or {}
    vram = _read(lambda: amdsmi.amdsmi_get_gpu_vram_usage(handle)) or {}
    activity = _read(lambda: amdsmi.amdsmi_get_gpu_activity(handle)) or {}
    power = _read(lambda: amdsmi.amdsmi_get_power_info(handle)) or {}
    name = asic.get("market_name") if isinstance(asic, dict) else None
    uuid = _read(lambda: amdsmi.amdsmi_get_gpu_device_uuid(handle))
    processes = _read(lambda: amdsmi.amdsmi_get_gpu_process_list(handle))
    return {
        "index": index,
        "uuid": uuid if uuid != "N/A" else None,
        "name": name if name != "N/A" else None,
        # MB, as NVIDIA's entries; an int, so the monitor's arithmetic works
        "memory.total": _whole_mb(vram.get("vram_total")),
        "memory.used": _whole_mb(vram.get("vram_used")),
        "utilization.gpu": _number(activity.get("gfx_activity")) if isinstance(activity, dict) else None,
        "power.draw": _amd_watts(power),
        "temperature.gpu": None,  # AMD's documented unit for the temperature is unclear; left unread until checked
        "processes": _amd_processes(processes) if processes is not None else None,
    }


def _whole_mb(value: Any) -> Optional[int]:
    number = _number(value)
    return None if number is None else int(number)


def _amd_watts(power: Any) -> Optional[float]:
    if not isinstance(power, dict):
        return None
    for key in ("current_socket_power", "average_socket_power"):
        value = _number(power.get(key))
        if value is not None:
            return value
    return None


def _amd_processes(processes: Any) -> List[Dict[str, Any]]:
    rows = []
    for process in processes or []:
        memory = process.get("memory_usage") if isinstance(process, dict) else None
        vram_bytes = _number(memory.get("vram_mem")) if isinstance(memory, dict) else None
        rows.append(
            {
                "pid": process.get("pid"),
                "command": process.get("name") if process.get("name") != "N/A" else "?",
                "gpu_memory_usage": int(vram_bytes / (1024 * 1024)) if vram_bytes is not None else None,
            }
        )
    return rows


_rocm_env_cache: Optional[bool] = None


def detect_rocm_env() -> bool:
    """True when this host has an AMD ROCm GPU and no NVIDIA GPU: the ROCm kernel driver is there, the NVIDIA one is not."""
    global _rocm_env_cache
    if _rocm_env_cache is not None:
        return _rocm_env_cache
    try:
        has_amd = os.path.exists("/dev/kfd")
        has_nvidia = os.path.exists("/dev/nvidia0") or os.path.exists("/proc/driver/nvidia")
        _rocm_env_cache = bool(has_amd and not has_nvidia)
    except Exception:
        _rocm_env_cache = False
    return _rocm_env_cache

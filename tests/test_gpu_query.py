"""The NVIDIA path of the GPU statistics: the query, the CUDA driver version, and the process memory of the monitor."""

import sys
from types import ModuleType, SimpleNamespace
from unittest import mock

import pytest

from cairnml.utilities import gpu_query
from cairnml.utilities.resource_monitor import ResourceMonitor


class _Gpu:
    def __init__(self, index, entry, processes=None):
        self.index = index
        self.entry = dict(entry, processes=processes)
        self.processes = processes


class _Collection:
    def __init__(self, gpus, driver_version=None):
        self.gpus = gpus
        self.driver_version = driver_version


@pytest.fixture
def fake_pynvml(monkeypatch):
    module = ModuleType("pynvml")
    module.nvmlInit = mock.Mock()
    module.nvmlShutdown = mock.Mock()
    monkeypatch.setitem(sys.modules, "pynvml", module)
    return module


def test_query_is_gpustats_new_query(monkeypatch):
    collection = _Collection([])
    monkeypatch.setattr(gpu_query, "new_query", mock.Mock(return_value=collection))
    assert gpu_query.query() is collection


@pytest.mark.parametrize(
    "raw, expected",
    [(12040, "12.4"), (11020, "11.2"), (12000, "12.0")],
)
def test_the_cuda_driver_version_is_major_and_minor(fake_pynvml, raw, expected):
    fake_pynvml.nvmlSystemGetCudaDriverVersion = lambda: raw
    assert gpu_query.driver_cuda_version() == expected


def test_the_cuda_driver_version_falls_back_to_the_v2_function(fake_pynvml):
    def missing():
        raise RuntimeError("not in this driver")

    fake_pynvml.nvmlSystemGetCudaDriverVersion = missing
    fake_pynvml.nvmlSystemGetCudaDriverVersion_v2 = lambda: 11040
    assert gpu_query.driver_cuda_version() == "11.4"


def test_no_cuda_driver_version_is_none(fake_pynvml):
    assert gpu_query.driver_cuda_version() is None


def test_nvml_is_initialised_for_the_call_and_shut_down_after(fake_pynvml):
    fake_pynvml.nvmlSystemGetCudaDriverVersion = lambda: 12040
    gpu_query.driver_cuda_version()
    fake_pynvml.nvmlInit.assert_called_once()
    fake_pynvml.nvmlShutdown.assert_called_once()


def _monitor(gpustat_module):
    monitor = ResourceMonitor.__new__(ResourceMonitor)
    monitor._task = SimpleNamespace(get_logger=lambda: SimpleNamespace(report_text=lambda *a, **k: None))
    monitor._gpustat = gpustat_module
    monitor._gpustat_fail = 0
    monitor._gpu_memory_per_process = True
    monitor._process_info = object()
    monitor._last_process_pool = {}
    monitor._report_frequency = 0
    monitor._last_process_id_list = [42]
    monitor._active_gpus = None
    monitor._default_gpu_utilization = 100
    monitor._gpu_utilization_warning_sent = True
    monitor._debug_mode = False
    return monitor


def test_a_process_without_readable_gpu_memory_counts_as_zero():
    process_without_memory = {"pid": 42, "gpu_memory_usage": None}
    gpu = _Gpu(0, {"utilization.gpu": 50, "memory.used": 1024, "memory.total": 2048}, [process_without_memory])
    stub = SimpleNamespace(query=mock.Mock(return_value=_Collection([gpu])))
    stats = _monitor(stub)._get_gpu_stats()
    assert stats["gpu_0_mem_used_gb"] == 0.0


class _LibraryNotFound(Exception):
    pass


class _DriverNotLoaded(Exception):
    pass


@pytest.fixture
def nvml_without_driver(fake_pynvml, monkeypatch):
    """A pynvml that has the exception classes of the real one; nvmlInit is set by each test."""
    fake_pynvml.NVMLError_LibraryNotFound = _LibraryNotFound
    fake_pynvml.NVMLError_DriverNotLoaded = _DriverNotLoaded
    monkeypatch.setattr(gpu_query, "amd_available", lambda: False)
    return fake_pynvml


@pytest.mark.parametrize("error", [_LibraryNotFound("NVML Shared Library Not Found"), _DriverNotLoaded("no driver")])
def test_a_machine_without_an_nvidia_driver_has_no_gpu_driver(nvml_without_driver, error):
    nvml_without_driver.nvmlInit.side_effect = error
    assert gpu_query.no_gpu_driver() is True


def test_an_amd_driver_counts_as_a_gpu_driver(nvml_without_driver, monkeypatch):
    nvml_without_driver.nvmlInit.side_effect = _LibraryNotFound("NVML Shared Library Not Found")
    monkeypatch.setattr(gpu_query, "amd_available", lambda: True)
    assert gpu_query.no_gpu_driver() is False


def test_a_working_nvidia_driver_is_a_gpu_driver_and_nvml_is_shut_down_again(nvml_without_driver):
    assert gpu_query.no_gpu_driver() is False
    nvml_without_driver.nvmlInit.assert_called_once()
    nvml_without_driver.nvmlShutdown.assert_called_once()


def test_any_other_nvml_error_is_left_to_the_normal_path(nvml_without_driver):
    nvml_without_driver.nvmlInit.side_effect = RuntimeError("driver/library version mismatch")
    assert gpu_query.no_gpu_driver() is False

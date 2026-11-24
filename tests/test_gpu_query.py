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

"""The AMD path of the GPU statistics, against a fake amdsmi module with the documented shapes (no AMD hardware here)."""

from unittest import mock

import pytest
from gpustat import GPUStatCollection

from cairnml.utilities import gpu_query


class FakeAmdSmi:
    def __init__(self):
        self.init_calls = 0
        self.shutdown_calls = 0
        self.handles = ["gpu-a", "gpu-b"]
        self.processes = {
            "gpu-a": [{"pid": 101, "name": "train", "memory_usage": {"vram_mem": 3 * 1024 * 1024 * 1024}}],
            "gpu-b": [{"pid": 202, "name": "N/A", "memory_usage": {"vram_mem": 512 * 1024 * 1024}}],
        }

    def amdsmi_init(self):
        self.init_calls += 1

    def amdsmi_shut_down(self):
        self.shutdown_calls += 1

    def amdsmi_get_processor_handles(self):
        return self.handles

    def amdsmi_get_gpu_driver_info(self, handle):
        return {"driver_version": "6.4.1"}

    def amdsmi_get_gpu_device_uuid(self, handle):
        return "UUID-" + handle

    def amdsmi_get_gpu_asic_info(self, handle):
        return {"market_name": "Instinct MI300X" if handle == "gpu-a" else "N/A"}

    def amdsmi_get_gpu_vram_usage(self, handle):
        if handle == "gpu-a":
            return {"vram_total": 196608, "vram_used": 4096}
        raise RuntimeError("not readable")

    def amdsmi_get_gpu_activity(self, handle):
        return {"gfx_activity": 37 if handle == "gpu-a" else "N/A"}

    def amdsmi_get_power_info(self, handle):
        return {"current_socket_power": "N/A", "average_socket_power": 212}

    def amdsmi_get_gpu_process_list(self, handle):
        return self.processes[handle]


@pytest.fixture
def amd(monkeypatch, tmp_path):
    fake = FakeAmdSmi()
    device = tmp_path / "kfd"
    device.write_text("")
    monkeypatch.setattr(gpu_query, "_KFD_DEVICE", str(device))
    monkeypatch.setattr(gpu_query, "_amd_state", {})
    monkeypatch.setattr(gpu_query, "_import_amdsmi", lambda: fake)
    monkeypatch.setattr(gpu_query, "_amd_shutdown", lambda amdsmi: None)
    return fake


def test_without_the_kernel_driver_amd_is_not_available_and_amdsmi_is_not_imported(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu_query, "_KFD_DEVICE", str(tmp_path / "missing"))
    importer = mock.Mock()
    monkeypatch.setattr(gpu_query, "_import_amdsmi", importer)
    assert gpu_query.amd_available() is False
    importer.assert_not_called()


def test_a_missing_amd_library_means_amd_is_not_available(monkeypatch, tmp_path):
    device = tmp_path / "kfd"
    device.write_text("")
    monkeypatch.setattr(gpu_query, "_KFD_DEVICE", str(device))
    monkeypatch.setattr(gpu_query, "_amd_state", {})

    def no_library():
        raise ImportError("libamd_smi.so not found")

    monkeypatch.setattr(gpu_query, "_import_amdsmi", no_library)
    assert gpu_query.amd_available() is False


def test_each_gpu_has_its_own_processes(amd):
    stats = gpu_query._amd_query()
    assert isinstance(stats, GPUStatCollection)
    first, second = stats.gpus
    assert [p["pid"] for p in first.processes] == [101]
    assert [p["pid"] for p in second.processes] == [202]


def test_not_available_values_are_none_and_a_failing_call_does_not_fail_the_query(amd):
    stats = gpu_query._amd_query()
    first, second = stats.gpus
    assert second.entry["utilization.gpu"] is None
    assert second.entry["name"] is None
    assert second.entry["memory.total"] is None
    assert second.entry["memory.used"] is None
    assert first.entry["utilization.gpu"] == 37.0
    assert first.entry["name"] == "Instinct MI300X"


def test_memory_is_in_mb_as_an_int_and_process_memory_in_mb(amd):
    stats = gpu_query._amd_query()
    first = stats.gpus[0]
    assert first.entry["memory.total"] == 196608
    assert isinstance(first.entry["memory.total"], int)
    assert first.processes[0]["gpu_memory_usage"] == 3 * 1024


def test_power_falls_back_to_the_average_when_the_current_is_not_available(amd):
    assert gpu_query._amd_query().gpus[0].entry["power.draw"] == 212.0


def test_the_driver_version_is_read(amd):
    assert gpu_query._amd_query().driver_version == "6.4.1"


def test_amd_is_initialised_once_across_queries(amd):
    gpu_query._amd_query()
    gpu_query._amd_query()
    assert amd.init_calls == 1


def test_query_falls_back_to_amd_when_nvidia_cannot_be_read(amd, monkeypatch):
    monkeypatch.setattr(gpu_query, "new_query", mock.Mock(side_effect=RuntimeError("NVML not found")))
    monkeypatch.setattr(gpu_query, "amd_available", lambda: True)
    stats = gpu_query.query()
    assert [g.entry["index"] for g in stats.gpus] == [0, 1]

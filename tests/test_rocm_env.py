import pytest

pytest.importorskip("gpustat", reason="needs the cairnml[gpu] extra")

from cairnml.utilities import gpu_query  # noqa: E402


def _detect(monkeypatch, existing):
    monkeypatch.setattr(gpu_query, "_rocm_env_cache", None)
    monkeypatch.setattr(gpu_query.os.path, "exists", lambda path: path in existing)
    return gpu_query.detect_rocm_env()


def test_an_amd_host_without_nvidia_is_a_rocm_host(monkeypatch):
    assert _detect(monkeypatch, {"/dev/kfd"}) is True


def test_a_host_with_nvidia_is_not_a_rocm_host(monkeypatch):
    assert _detect(monkeypatch, {"/dev/kfd", "/dev/nvidia0"}) is False


def test_a_host_without_a_gpu_is_not_a_rocm_host(monkeypatch):
    assert _detect(monkeypatch, set()) is False

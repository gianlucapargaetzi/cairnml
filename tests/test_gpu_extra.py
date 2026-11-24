"""GPU monitoring comes from NVIDIA's own bindings (base dependencies); when they are missing the task log names them."""

import importlib
import pathlib
import sys

import pytest

import cairnml
from cairnml.utilities.resource_monitor import gpu_unavailable_message


def test_the_gpu_reader_uses_the_installed_nvidia_bindings_not_a_copy():
    from cairnml.utilities import gpu_query

    module_file = pathlib.Path(gpu_query.new_query.__globals__["N"].__file__).resolve()
    assert gpu_query.new_query.__globals__["N"].__name__ == "pynvml"
    assert pathlib.Path(cairnml.__file__).resolve().parent not in module_file.parents


def test_a_missing_nvidia_binding_names_the_packages():
    error = ModuleNotFoundError("No module named 'pynvml'", name="pynvml")
    assert gpu_unavailable_message(error) == (
        "CairnML Monitor: GPU monitoring is not available, because the NVIDIA bindings are missing: pip install nvidia-ml-py gpustat"
    )


HINT = "CairnML Monitor: GPU monitoring is not available, because the NVIDIA bindings are missing: pip install nvidia-ml-py gpustat"


def test_a_missing_gpustat_names_the_packages():
    assert gpu_unavailable_message(ModuleNotFoundError("No module named 'gpustat'", name="gpustat")) == HINT


@pytest.mark.parametrize("name", ["gpustat.core", "pynvml.nvml"])
def test_a_submodule_of_a_nvidia_package_names_the_packages(name):
    assert gpu_unavailable_message(ImportError("missing", name=name)) == HINT


def test_the_real_gpu_reader_import_error_names_the_packages(monkeypatch):
    # gpustat is blocked, so importing the real reader raises the error the monitor receives
    monkeypatch.setitem(sys.modules, "gpustat", None)
    monkeypatch.delitem(sys.modules, "cairnml.utilities.gpu_query", raising=False)
    with pytest.raises(ImportError) as raised:
        importlib.import_module("cairnml.utilities.gpu_query")
    assert gpu_unavailable_message(raised.value) == HINT


def test_a_wrapped_import_error_names_the_packages_through_its_cause():
    # gpustat re-raises a missing pynvml as an ImportError without a name, keeping the original as the cause
    try:
        try:
            raise ModuleNotFoundError("No module named 'pynvml'", name="pynvml")
        except ModuleNotFoundError as original:
            raise ImportError("gpustat could not load NVML") from original
    except ImportError as wrapped:
        error = wrapped
    assert gpu_unavailable_message(error) == HINT


def test_any_other_reason_gives_the_plain_message():
    assert gpu_unavailable_message(None) == "CairnML Monitor: GPU monitoring is not available"
    assert gpu_unavailable_message(ImportError("boom", name="something_else")) == (
        "CairnML Monitor: GPU monitoring is not available"
    )


def test_the_message_does_not_send_the_user_to_an_extra():
    assert "cairnml[" not in gpu_unavailable_message(ModuleNotFoundError("No module named 'gpustat'", name="gpustat"))

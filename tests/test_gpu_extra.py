"""GPU monitoring comes from NVIDIA's own bindings (the extra cairnml[gpu]); its absence is named in the task log."""

import pathlib

import pytest

import cairnml
from cairnml.utilities.resource_monitor import gpu_unavailable_message


def test_the_gpu_reader_uses_the_installed_nvidia_bindings_not_a_copy():
    pytest.importorskip("pynvml", reason="needs the cairnml[gpu] extra")
    from cairnml.utilities.gpu import gpustat

    module_file = pathlib.Path(gpustat.N.__file__).resolve()
    assert gpustat.N.__name__ == "pynvml"
    assert pathlib.Path(cairnml.__file__).resolve().parent not in module_file.parents


def test_a_missing_nvidia_binding_names_the_extra():
    error = ModuleNotFoundError("No module named 'pynvml'", name="pynvml")
    assert gpu_unavailable_message(error) == (
        'CairnML Monitor: GPU monitoring is not available, because the NVIDIA bindings are missing: pip install "cairnml[gpu]"'
    )


def test_any_other_reason_gives_the_plain_message():
    assert gpu_unavailable_message(None) == "CairnML Monitor: GPU monitoring is not available"
    assert gpu_unavailable_message(ImportError("boom", name="something_else")) == (
        "CairnML Monitor: GPU monitoring is not available"
    )

"""What the SDK detects as a task's requirements is pinned on six projects, against a fixed table of installed packages.

The table is fixed so the expectations do not depend on what the test machine has installed.
"""

import logging

import pytest

from cairnml.utilities.project_requirements import GenerateReqs

IGNORES = [".git", ".hg", ".idea", "__pycache__", ".ipynb_checkpoints", "site-packages", "dist-packages"]

INSTALLED = {
    "requests": ("requests", "2.31.0"),
    "yaml": ("PyYAML", "6.0.1"),
    "numpy": ("numpy", "1.26.4"),
    "google": {"protobuf": ("protobuf", "4.25.1")},
}


def scan(project, entry=None):
    generator = GenerateReqs(project_path=str(project), ignores=IGNORES, installed_pkgs=INSTALLED)
    reqs, try_imports, guess, local = generator.extract_reqs(entry_point_filename=entry)
    return {
        "reqs": {name: detail.version for name, detail in reqs.sorted_items()},
        "try": sorted(try_imports),
        "guess": sorted(guess.keys()),
        "local": sorted(local.keys()),
    }


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_local_modules_are_not_requirements(tmp_path):
    write(tmp_path / "main.py", "import helper\nimport requests\n")
    write(tmp_path / "helper.py", "import numpy\n")
    assert scan(tmp_path) == EXPECTED["local_modules"]


def test_try_except_imports_are_reported_as_try_imports(tmp_path):
    write(tmp_path / "main.py", "try:\n    import ujson\nexcept ImportError:\n    import yaml\n")
    assert scan(tmp_path) == EXPECTED["try_except"]


def test_a_namespace_package_is_mapped_to_its_distribution(tmp_path):
    write(tmp_path / "main.py", "from google.protobuf import message\n")
    assert scan(tmp_path) == EXPECTED["namespace"]


def test_an_entry_script_that_imports_local_modules_scans_the_project(tmp_path):
    write(tmp_path / "train.py", "import utils\nimport numpy\n")
    write(tmp_path / "utils.py", "import yaml\n")
    write(tmp_path / "sub" / "unused.py", "import requests\n")
    assert scan(tmp_path, entry="train.py") == EXPECTED["entry_script"]


def test_an_import_that_is_not_installed_is_a_guess(tmp_path):
    write(tmp_path / "main.py", "import notinstalledpkg\nimport requests\n")
    assert scan(tmp_path) == EXPECTED["not_installed"]


def test_a_notebook_code_string_is_scanned_as_a_file(tmp_path):
    from cairnml.utilities.project_requirements import file_import_modules

    modules, try_imports = file_import_modules(
        "notebook.ipynb", b"import numpy\ntry:\n    import ujson\nexcept ImportError:\n    pass\n"
    )
    assert sorted(modules.keys()) == EXPECTED["notebook"]["modules"]
    assert sorted(try_imports) == EXPECTED["notebook"]["try"]


# Expected requirements. Recorded from the embedded pigar copy on d509740b, then changed on purpose in slice 2, one
# difference at a time:
#  - local_modules: "helper" is a module of the project, so it is no longer a guess (it was listed as not installed).
#  - entry_script: the entry script imports the project's utils.py, so the whole project is scanned, as the old code
#    did; that reads sub/unused.py too, and PyYAML (from utils.py) and requests are now recorded.
EXPECTED = {
    "local_modules": {
        "reqs": {"numpy": "1.26.4", "requests": "2.31.0"},
        "try": [],
        "guess": [],
        "local": [],
    },
    "try_except": {
        "reqs": {"PyYAML": "6.0.1"},
        "try": ["ujson", "yaml"],
        "guess": ["ujson"],
        "local": [],
    },
    "namespace": {
        "reqs": {"protobuf": "4.25.1"},
        "try": [],
        "guess": [],
        "local": [],
    },
    "entry_script": {
        "reqs": {"PyYAML": "6.0.1", "numpy": "1.26.4", "requests": "2.31.0"},
        "try": [],
        "guess": [],
        "local": [],
    },
    "not_installed": {
        "reqs": {"requests": "2.31.0"},
        "try": [],
        "guess": ["notinstalledpkg"],
        "local": [],
    },
    "notebook": {"modules": ["numpy", "ujson"], "try": ["ujson"]},
}


def test_a_scan_leaves_the_sys_path_and_sys_modules_of_the_calling_process_unchanged(tmp_path):
    import sys

    write(tmp_path / "main.py", "import helper\nimport requests\nimport notinstalledpkg\n")
    write(tmp_path / "helper.py", "import numpy\n")
    path_before = list(sys.path)
    modules_before = set(sys.modules)
    scan(tmp_path)
    assert sys.path == path_before
    assert set(sys.modules) == modules_before


# --- a failed scan is reported as an error with its cause (AC-2) -------------------------------------------------------
LOGGER = "cairnml.Repository Detection"


def _worker_stub(tmp_path, monkeypatch, stderr_text):
    from cairnml.utilities import project_requirements

    stub = tmp_path / "failing_worker.py"
    stub.write_text("import sys\nsys.stderr.write({!r})\nsys.exit(1)\n".format(stderr_text))
    monkeypatch.setattr(project_requirements, "WORKER", str(stub))


@pytest.fixture
def scan_log():
    """The records of the detection logger, captured on the logger itself (the SDK sets its own propagation)."""
    records = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collect(level=logging.DEBUG)
    logger = logging.getLogger(LOGGER)
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)


def _errors(records):
    return [r for r in records if r.levelno == logging.ERROR]


def test_worker_failure_is_logged_as_error(tmp_path, monkeypatch, scan_log):
    from cairnml.backend_interface.task.repo.scriptinfo import ScriptRequirements

    write(tmp_path / "train.py", "import numpy\n")
    _worker_stub(tmp_path, monkeypatch, "Traceback (most recent call last):\nRuntimeError: scanner broke\n")
    result = ScriptRequirements(str(tmp_path)).get_requirements(entry_point_filename="train.py")
    assert result == ("", "")
    errors = _errors(scan_log)
    assert len(errors) == 1
    assert "RuntimeError: scanner broke" in errors[0].getMessage()
    assert "pass packages=" in errors[0].getMessage().lower()


def test_pigar_missing_in_child_is_logged_as_error(tmp_path, monkeypatch, scan_log):
    from cairnml.backend_interface.task.repo.scriptinfo import ScriptRequirements

    write(tmp_path / "train.py", "import numpy\n")
    _worker_stub(tmp_path, monkeypatch, "ModuleNotFoundError: No module named 'pigar'\n")
    result = ScriptRequirements(str(tmp_path)).get_requirements(entry_point_filename="train.py")
    assert result == ("", "")
    errors = _errors(scan_log)
    assert len(errors) == 1
    assert "No module named 'pigar'" in errors[0].getMessage()
    assert "install pigar" in errors[0].getMessage()


# --- the imports of a function step are cross-checked against the scan (AC-3) -----------------------------------------
def _step_source(body):
    return "def train():\n" + body + "\n"


def test_function_body_imports_are_scanned(tmp_path):
    # guard: the scan itself sees an import inside a function body (green before the cross-check)
    write(tmp_path / "step.py", _step_source("    import numpy\n    return numpy"))
    assert scan(tmp_path, entry="step.py")["reqs"] == {"numpy": "1.26.4"}


def test_cross_check_flags_a_missing_import(scan_log):
    from cairnml.backend_interface.task.populate import log_step_imports_not_scanned

    source = _step_source("    import torch\n    return torch")
    missing = log_step_imports_not_scanned("train", source, scanned=set())
    assert missing == ["torch"]
    errors = _errors(scan_log)
    assert len(errors) == 1
    assert "torch" in errors[0].getMessage()
    assert "packages=" in errors[0].getMessage().lower()


MUST_NOT_LOG = {
    "sklearn_renamed_by_callback": (
        "    import sklearn\n    from sklearn.linear_model import Ridge\n    return Ridge",
        {},
    ),
    "try_except_import_error": (
        "    try:\n        import ujson\n    except ImportError:\n        ujson = None\n    return ujson",
        {},
    ),
    "stdlib": ("    import json\n    from pathlib import Path\n    return json, Path", {}),
    "relative": ("    from . import helper\n    return helper", {}),
    "dotted": ("    import google.protobuf.message\n    return google", {}),
    "cairnml_itself": ("    import cairnml.datasets\n    return cairnml", {}),
    "nested_in_if": ("    if FLAG:\n        import requests\n    return 1", {}),
    "local_module": ("    from helpers import clean\n    return clean", {"helpers.py": "def clean():\n    return 1\n"}),
}


@pytest.mark.parametrize("case", sorted(MUST_NOT_LOG))
def test_cross_check_does_not_log(tmp_path, scan_log, case):
    from cairnml.backend_interface.task.populate import log_step_imports_not_scanned
    from cairnml.backend_interface.task.repo.scriptinfo import ScriptRequirements

    body, extra = MUST_NOT_LOG[case]
    source = _step_source(body)
    write(tmp_path / "step.py", source)
    for name, text in extra.items():
        write(tmp_path / name, text)
    scanner = ScriptRequirements(str(tmp_path))
    scanner.get_requirements(entry_point_filename="step.py")
    assert scanner.found_names is not None
    assert log_step_imports_not_scanned("train", source, scanned=scanner.found_names) == []
    assert _errors(scan_log) == []

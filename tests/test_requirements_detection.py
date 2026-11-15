"""What the SDK detects as a task's requirements is pinned on six projects, against a fixed table of installed packages.

The table is fixed so the expectations do not depend on what the test machine has installed.
"""

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

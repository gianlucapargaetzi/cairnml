"""Job containers install the agent and the SDK from wheels built out of the source trees of the machine. The check that
a folder is such a source tree must understand pyproject.toml, which replaced setup.py, and must not be fooled by the
pyproject.toml of another project (a virtual environment often sits inside one)."""

from pathlib import Path

import pytest

from cairnml_agent.commands import worker
from cairnml_agent.errors import CommandFailedError


def tree(folder: Path, pyproject: str = None, setup_py: bool = False) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    if pyproject is not None:
        (folder / "pyproject.toml").write_text(pyproject)
    if setup_py:
        (folder / "setup.py").write_text("from setuptools import setup\nsetup()\n")
    return folder


def test_a_pyproject_that_names_the_package_is_a_source_tree(tmp_path):
    folder = tree(tmp_path, '[project]\nname = "cairnml-agent"\nversion = "0.0.1"\n')
    assert worker.is_source_tree(folder, "cairnml-agent")


def test_the_pyproject_of_another_project_is_not(tmp_path):
    folder = tree(tmp_path, '[project]\nname = "my-own-project"\n')
    assert not worker.is_source_tree(folder, "cairnml")
    assert not worker.is_source_tree(folder, "cairnml-agent")


def test_a_package_whose_name_only_starts_the_same_is_not(tmp_path):
    folder = tree(tmp_path, '[project]\nname = "cairnml-agent"\n')
    assert not worker.is_source_tree(folder, "cairnml")


def test_an_older_tree_with_a_setup_py_still_counts(tmp_path):
    assert worker.is_source_tree(tree(tmp_path, setup_py=True), "cairnml")


def test_an_empty_folder_is_not_a_source_tree(tmp_path):
    assert not worker.is_source_tree(tmp_path, "cairnml")
    with pytest.raises(CommandFailedError, match="not a source tree of cairnml"):
        worker.build_local_wheel(tmp_path, "cairnml")


def test_the_real_source_tree_of_this_agent_is_recognised():
    root = Path(worker.__file__).resolve().parents[2]
    if not (root / "pyproject.toml").is_file():
        pytest.skip("not run from a source tree")
    assert worker.is_source_tree(root, "cairnml-agent")


def test_a_read_only_source_tree_still_builds(tmp_path):
    """The agent may run from a tree its user cannot write (the job containers still need its wheel)."""
    import os
    import stat

    source = tmp_path / "cairnml-agent"
    package = source / "cairnml_agent"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (source / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n\n'
        '[project]\nname = "cairnml-agent"\nversion = "0.0.1"\n'
    )
    for root, dirs, files in os.walk(source):
        for name in dirs + files:
            os.chmod(os.path.join(root, name), stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)
        os.chmod(root, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)

    wheel = worker.build_local_wheel(source, "cairnml-agent")
    assert wheel.endswith(".whl")
    assert not (source / "cairnml_agent.egg-info").exists()

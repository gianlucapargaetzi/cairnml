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
    assert worker.is_source_tree(root, "cairnml")


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


# ---- an installed distribution is repacked, a source tree is built (issue #145) -----------------------------------


class FakeDistribution:
    """An installed distribution as far as the wheel functions look: a name, a version and a root folder."""

    def __init__(self, name, version, root):
        self.metadata = {"Name": name}
        self.version = version
        self._root = root

    def locate_file(self, path):
        return self._root / path


@pytest.fixture
def fresh_wheels(monkeypatch):
    monkeypatch.setattr(worker, "_LOCAL_WHEELS", {})


def test_an_installed_agent_is_repacked_and_no_source_tree_is_built(monkeypatch, tmp_path, fresh_wheels):
    dist = FakeDistribution("cairnml-agent", "0.0.6", tmp_path / "site-packages")
    repacked = []
    monkeypatch.setattr(worker, "installed_distribution", lambda name, package_dir: dist)
    monkeypatch.setattr(worker, "wheel_from_installed", lambda d, out: repacked.append(d) or "/x/agent.whl")

    def no_build(*args):
        raise AssertionError("a source tree was built although the agent is installed")

    monkeypatch.setattr(worker, "build_local_wheel", no_build)
    assert worker.local_agent_wheel() == "/x/agent.whl"
    assert repacked == [dist]


def test_an_agent_that_is_not_installed_is_built_from_its_source_tree(monkeypatch, fresh_wheels):
    built = []
    monkeypatch.setattr(worker, "installed_distribution", lambda name, package_dir: None)
    monkeypatch.setattr(worker, "build_local_wheel", lambda source, name: built.append((source, name)) or "/x/a.whl")
    assert worker.local_agent_wheel() == "/x/a.whl"
    assert built == [(Path(worker.__file__).resolve().parents[2], "cairnml-agent")]


def test_an_installed_sdk_is_repacked_even_inside_a_venv_of_its_own_checkout(monkeypatch, tmp_path, fresh_wheels):
    """A virtual environment sits inside the SDK's checkout: the checkout must not be the one built."""
    import cairnml

    checkout = tmp_path / "cairnml"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text('[project]\nname = "cairnml"\nversion = "0.0.9"\n')
    site = checkout / "venv" / "lib" / "python3.12" / "site-packages"
    dist = FakeDistribution("cairnml", "0.0.9", site)
    monkeypatch.setattr(cairnml, "__path__", [str(site / "cairnml")])
    monkeypatch.setattr(worker, "installed_distribution", lambda name, package_dir: dist)
    monkeypatch.setattr(worker, "wheel_from_installed", lambda d, out: "/x/sdk.whl")

    def no_build(*args):
        raise AssertionError("the checkout was built although the SDK is installed")

    monkeypatch.setattr(worker, "build_local_wheel", no_build)
    assert worker.local_sdk_wheel() == "/x/sdk.whl"


def test_an_editable_sdk_is_built_from_its_checkout(monkeypatch, tmp_path, fresh_wheels):
    import cairnml

    checkout = tmp_path / "cairnml"
    (checkout / "cairnml").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text('[project]\nname = "cairnml"\nversion = "0.0.9"\n')
    monkeypatch.setattr(cairnml, "__path__", [str(checkout / "cairnml")])
    monkeypatch.setattr(worker, "installed_distribution", lambda name, package_dir: None)
    built = []
    monkeypatch.setattr(worker, "build_local_wheel", lambda source, name: built.append((source, name)) or "/x/s.whl")
    assert worker.local_sdk_wheel() == "/x/s.whl"
    assert built == [(checkout.resolve(), "cairnml")]


def test_an_sdk_that_is_neither_installed_nor_a_checkout_says_so(monkeypatch, tmp_path, fresh_wheels):
    import cairnml

    monkeypatch.setattr(cairnml, "__path__", [str(tmp_path / "nowhere" / "cairnml")])
    monkeypatch.setattr(worker, "installed_distribution", lambda name, package_dir: None)
    with pytest.raises(CommandFailedError, match="neither an installed distribution nor a source tree"):
        worker.local_sdk_wheel()

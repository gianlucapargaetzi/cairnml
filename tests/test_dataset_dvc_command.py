"""The dvc that runs is the one next to the interpreter, then the one on PATH, then the dvc module of the interpreter."""

import importlib.util
import os
import sys
from pathlib import Path

import dvc
import pytest

from cairnml.datasets.dvcutil import Dvc
from cairnml.datasets.errors import DatasetError


def _write_script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def _write_fake_dvc(directory: Path, label: str) -> Path:
    return _write_script(directory / "dvc", f"echo {label}")


def _fake_interpreter(tmp_path: Path) -> Path:
    """An interpreter directory with no dvc script beside it."""
    return _write_script(tmp_path / "venv" / "bin" / "python", "exit 0")


def test_dvc_next_to_the_interpreter_is_used_before_the_one_on_path(tmp_path, monkeypatch):
    fake_python = _fake_interpreter(tmp_path)
    _write_fake_dvc(fake_python.parent, "beside-interpreter")
    _write_fake_dvc(tmp_path / "system" / "bin", "on-path")
    monkeypatch.setattr(sys, "executable", str(fake_python))
    monkeypatch.setenv("PATH", str(tmp_path / "system" / "bin"))

    assert Dvc(tmp_path).run("status") == "beside-interpreter"


def test_dvc_on_path_is_used_when_none_is_next_to_the_interpreter(tmp_path, monkeypatch):
    fake_python = _fake_interpreter(tmp_path)
    _write_fake_dvc(tmp_path / "system" / "bin", "on-path")
    monkeypatch.setattr(sys, "executable", str(fake_python))
    monkeypatch.setenv("PATH", str(tmp_path / "system" / "bin"))

    assert Dvc(tmp_path).run("status") == "on-path"


def test_the_dvc_module_of_the_interpreter_is_used_when_there_is_no_binary(tmp_path, monkeypatch):
    # The wrapper has no dvc beside it and PATH has none, but it runs the real interpreter, which has the dvc module.
    wrapper = _write_script(tmp_path / "venv" / "bin" / "python", f'exec "{sys.executable}" "$@"')
    monkeypatch.setattr(sys, "executable", str(wrapper))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert importlib.util.find_spec("dvc") is not None

    assert dvc.__version__ in Dvc(tmp_path).run("version")


def test_missing_dvc_error_names_both_places_searched_and_the_module(tmp_path, monkeypatch):
    fake_python = _fake_interpreter(tmp_path)
    empty_path = tmp_path / "empty"
    empty_path.mkdir()
    monkeypatch.setattr(sys, "executable", str(fake_python))
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    with pytest.raises(DatasetError) as raised:
        Dvc(tmp_path).run("status")

    message = str(raised.value)
    assert str(fake_python.parent / "dvc") in message
    assert "PATH" in message
    assert "not installed" in message
    assert "cairnml[dataset]" in message


def test_the_interpreter_dvc_is_not_used_when_it_is_not_executable(tmp_path, monkeypatch):
    fake_python = _fake_interpreter(tmp_path)
    not_executable = fake_python.parent / "dvc"
    not_executable.write_text("#!/bin/sh\necho beside-interpreter\n")
    not_executable.chmod(0o644)
    _write_fake_dvc(tmp_path / "system" / "bin", "on-path")
    monkeypatch.setattr(sys, "executable", str(fake_python))
    monkeypatch.setenv("PATH", str(tmp_path / "system" / "bin"))

    assert Dvc(tmp_path).run("status") == "on-path"
    assert os.access(not_executable, os.X_OK) is False

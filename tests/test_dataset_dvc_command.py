"""The dvc binary is the one next to the running interpreter, then the one on PATH, and a missing one is reported."""

import os
import sys
from pathlib import Path

import pytest

from cairnml.datasets.dvcutil import Dvc
from cairnml.datasets.errors import DatasetError


def _write_fake_dvc(directory: Path, label: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "dvc"
    script.write_text(f"#!/bin/sh\necho {label}\n")
    script.chmod(0o755)
    return script


def _fake_interpreter(tmp_path: Path) -> Path:
    interpreter_dir = tmp_path / "venv" / "bin"
    interpreter_dir.mkdir(parents=True)
    fake_python = interpreter_dir / "python"
    fake_python.write_text("#!/bin/sh\nexit 0\n")
    fake_python.chmod(0o755)
    return fake_python


def test_dvc_next_to_the_interpreter_is_used_before_the_one_on_path(tmp_path, monkeypatch):
    fake_python = _fake_interpreter(tmp_path)
    interpreter_dir = fake_python.parent
    _write_fake_dvc(interpreter_dir, "beside-interpreter")
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


def test_missing_dvc_error_names_both_places_searched(tmp_path, monkeypatch):
    fake_python = _fake_interpreter(tmp_path)
    interpreter_dir = fake_python.parent
    empty_path = tmp_path / "empty"
    empty_path.mkdir()
    monkeypatch.setattr(sys, "executable", str(fake_python))
    monkeypatch.setenv("PATH", str(empty_path))

    with pytest.raises(DatasetError) as raised:
        Dvc(tmp_path).run("status")

    message = str(raised.value)
    assert str(interpreter_dir / "dvc") in message
    assert "PATH" in message
    assert "cairnml[dataset]" in message


def test_the_interpreter_dvc_is_not_used_when_it_is_not_executable(tmp_path, monkeypatch):
    fake_python = _fake_interpreter(tmp_path)
    interpreter_dir = fake_python.parent
    not_executable = interpreter_dir / "dvc"
    not_executable.write_text("#!/bin/sh\necho beside-interpreter\n")
    not_executable.chmod(0o644)
    _write_fake_dvc(tmp_path / "system" / "bin", "on-path")
    monkeypatch.setattr(sys, "executable", str(fake_python))
    monkeypatch.setenv("PATH", str(tmp_path / "system" / "bin"))

    assert Dvc(tmp_path).run("status") == "on-path"
    assert os.access(not_executable, os.X_OK) is False

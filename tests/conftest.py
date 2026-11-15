"""Shared fixtures: an Artifacts manager that runs without any server or storage."""

import os
import tempfile
from pathlib import Path

import pytest

from cairnml.backend_api import Session
from cairnml.binding import artifacts as artifacts_module


class _RecordingLogger:
    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, message, *args, **kwargs):
        self.infos.append(str(message))

    def warning(self, message, *args, **kwargs):
        self.warnings.append(str(message))


class _FakeTask:
    output_uri = "s3://bucket/output"

    def __init__(self):
        self.added = []

    def _add_artifacts(self, artifact_list):
        self.added.append(list(artifact_list))


class ArtifactsEnv:
    """An Artifacts manager whose uploader records its arguments and the uploaded bytes."""

    def __init__(self, tmp_dir):
        self.tmp_dir = tmp_dir
        self.task = _FakeTask()
        self.log = _RecordingLogger()
        self.uploads = []
        self.manager = artifacts_module.Artifacts(self.task)
        self.manager._upload_local_file = self._fake_upload

    def _fake_upload(
        self,
        local_file,
        name,
        delete_after_upload=False,
        override_filename=None,
        override_filename_ext=None,
        wait_on_upload=False,
    ):
        path = self.manager._pop_temp_file(local_file)
        with open(str(path), "rb") as handle:
            data = handle.read()
        self.uploads.append(
            dict(
                name=name,
                data=data,
                delete_after_upload=delete_after_upload,
                override_filename=override_filename,
                override_filename_ext=override_filename_ext,
                wait_on_upload=wait_on_upload,
            )
        )
        if delete_after_upload:
            os.unlink(str(path))
        return "s3://bucket/{}/{}".format(name, override_filename)

    def upload(self, *args, **kwargs):
        return self.manager.upload_artifact(*args, **kwargs)

    @property
    def artifact(self):
        """The artifact entry that the last successful upload added to the task."""
        return self.task.added[-1][-1]

    @property
    def artifacts(self):
        return self.task.added[-1] if self.task.added else []

    def leftovers(self):
        """Files and folders still present in the private temp directory."""
        return sorted(str(p.relative_to(self.tmp_dir)) for p in Path(self.tmp_dir).rglob("*"))


@pytest.fixture
def artifacts_env(tmp_path, monkeypatch):
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_dir))
    monkeypatch.setattr(Session, "check_min_api_version", classmethod(lambda cls, version: True))
    env = ArtifactsEnv(tmp_dir)
    monkeypatch.setattr(artifacts_module.LoggerRoot, "get_base_logger", classmethod(lambda cls, *a, **k: env.log))
    return env

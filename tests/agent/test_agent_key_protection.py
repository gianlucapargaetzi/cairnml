"""A job cannot reach the agent's device key through the folders, mounts or environment it is given."""

import os
import stat
from pathlib import Path

import pytest

from cairnml_agent.commands.worker import check_job_mounts, job_environment
from cairnml_agent.idp.device import DEVICE_KEY_PATH, exposes_device_key, secure_folder
from cairnml_agent.idp.enrol import _replace_key
from cairnml_agent.idp.keys import generate_private_key


def test_the_agent_folder_is_readable_by_its_owner_only(tmp_path):
    folder = tmp_path / "home" / ".cairnml" / "agent"
    folder.mkdir(parents=True)
    os.chmod(folder, 0o755)
    secure_folder(folder)
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700


def test_an_enrolled_key_is_written_into_a_private_folder(tmp_path):
    key_path = tmp_path / "agent" / "device.key"
    _replace_key(generate_private_key(), key_path)
    assert stat.S_IMODE(os.stat(key_path.parent).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600


def test_mounting_the_key_its_folder_or_a_folder_above_exposes_the_key(tmp_path):
    key = tmp_path / "home" / ".cairnml" / "agent" / "device.key"
    for folder in (key, key.parent, key.parent.parent, key.parent.parent.parent, tmp_path, Path("/")):
        assert exposes_device_key(folder, key_path=key), folder


def test_mounting_other_folders_of_the_agent_home_does_not_expose_the_key(tmp_path):
    key = tmp_path / "home" / ".cairnml" / "agent" / "device.key"
    for folder in (key.parent.parent / "pip-cache", key.parent.parent / "venvs-builds", tmp_path / "data"):
        assert not exposes_device_key(folder, key_path=key), folder


def test_the_default_job_folders_do_not_expose_the_key():
    defaults = ["~/.cairnml/apt-cache", "~/.cairnml/pip-cache", "~/.cairnml/poetry-cache",
                "~/.cairnml/data_cache", "~/.cairnml/vcs-cache", "~/.cairnml/pip-download-cache",
                "~/.cairnml/venvs-builds", "~/.cairnml/venvs-cache"]
    check_job_mounts(defaults)
    for folder in defaults:
        assert not exposes_device_key(folder), folder


def test_a_job_folder_that_holds_the_key_is_refused():
    with pytest.raises(ValueError, match="device key"):
        check_job_mounts(["~/.cairnml/pip-cache", str(DEVICE_KEY_PATH.parent)])
    with pytest.raises(ValueError, match="device key"):
        check_job_mounts([str(Path.home())])


def test_unset_job_folders_are_not_checked():
    check_job_mounts([None, ""])


def test_the_job_gets_its_own_token_and_never_the_agents_auth_token():
    base = {"PATH": "/usr/bin", "CAIRNML_AUTH_TOKEN": "agent-admin-token"}
    env = job_environment(base, job_token="job-1", job_token_file="/tmp/job_token")
    assert "CAIRNML_AUTH_TOKEN" not in env
    assert env["CAIRNML_JOB_TOKEN"] == "job-1"
    assert env["CAIRNML_JOB_TOKEN_FILE"] == "/tmp/job_token"
    assert env["PATH"] == "/usr/bin"
    assert base["CAIRNML_AUTH_TOKEN"] == "agent-admin-token", "the agent's own environment is not changed"


def test_an_impersonated_job_gets_the_task_owners_token():
    env = job_environment({"CAIRNML_AUTH_TOKEN": "agent-admin-token"}, impersonated_token="owner-token")
    assert env["CAIRNML_AUTH_TOKEN"] == "owner-token"
    assert "CAIRNML_JOB_TOKEN" not in env

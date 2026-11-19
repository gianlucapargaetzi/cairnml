"""Two places of the agent's session used to swallow every exception: the CUDA detection at start-up and the creation
of the cache folders. An error of the detection is the agent's own and is raised; a folder that cannot be created is
reported (OSError only) and the others are still made.
"""

import logging

import pytest

from cairnml_agent.helper.package.requirements import RequirementsManager
from cairnml_agent.session import Session


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A scratch home, and the environment variables the session sets restored afterwards."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("CUDA_DEVICE_ORDER", "CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    return tmp_path


def session(**kwargs):
    return Session(only_load_config=True, **kwargs)


def test_the_cuda_versions_of_the_detection_are_put_into_the_configuration(home, monkeypatch):
    monkeypatch.setattr(RequirementsManager, "get_cuda_version", staticmethod(lambda config: ("112", "8")))

    started = session()

    assert started.config["agent"]["cuda_version"] == "112"
    assert started.config["agent"]["cudnn_version"] == "8"


def test_a_cpu_only_agent_has_no_cuda_and_does_not_detect_any(home, monkeypatch):
    def not_called(config):
        raise AssertionError("a cpu-only agent must not detect CUDA")

    monkeypatch.setattr(RequirementsManager, "get_cuda_version", staticmethod(not_called))

    started = session(cpu_only=True)

    assert (started.config["agent"]["cuda_version"], started.config["agent"]["cudnn_version"]) == ("0", "0")


def test_an_error_of_the_cuda_detection_stops_the_start_instead_of_leaving_the_versions_unset(home, monkeypatch):
    def broken(config):
        raise RuntimeError("the detection is broken")

    monkeypatch.setattr(RequirementsManager, "get_cuda_version", staticmethod(broken))

    with pytest.raises(RuntimeError, match="the detection is broken"):
        session()


@pytest.fixture
def started(home, monkeypatch):
    monkeypatch.setattr(RequirementsManager, "get_cuda_version", staticmethod(lambda config: ("0", "0")))
    return session()


def test_the_cache_folders_are_created(started, home):
    started.config.put("agent.venvs_dir", str(home / "caches" / "venvs"))

    started.create_cache_folders()

    assert (home / "caches" / "venvs").is_dir()


def test_a_folder_that_cannot_be_created_is_reported_and_the_others_are_still_made(started, home, caplog):
    blocker = home / "blocker"
    blocker.write_text("a file, not a folder")
    started.config.put("agent.venvs_dir", str(blocker / "venvs"))
    started.config.put("agent.vcs_cache.path", str(home / "vcs"))

    with caplog.at_level(logging.WARNING):
        started.create_cache_folders()

    assert (home / "vcs").is_dir()
    assert any(
        record.levelno == logging.WARNING and str(blocker / "venvs") in record.getMessage() for record in caplog.records
    )


def test_an_error_that_is_not_about_the_file_system_is_raised(started, home, monkeypatch):
    def broken(self, *args, **kwargs):
        raise RuntimeError("not a file system error")

    started.config.put("agent.venvs_dir", str(home / "venvs"))
    monkeypatch.setattr("pathlib.Path.mkdir", broken)

    with pytest.raises(RuntimeError, match="not a file system error"):
        started.create_cache_folders()

"""The agent runs jobs in Docker unless it is started with --allow-venv."""

import pytest

from cairnml_agent.commands.worker import check_execution_mode, run_mode_for


def test_docker_mode_is_accepted():
    check_execution_mode(use_docker=True, allow_venv=False)


def test_venv_mode_is_refused_by_default():
    with pytest.raises(ValueError, match="Docker containers only"):
        check_execution_mode(use_docker=False, allow_venv=False)


def test_venv_mode_is_accepted_only_with_the_flag():
    check_execution_mode(use_docker=False, allow_venv=True)


def test_venv_mode_with_the_flag_warns_about_the_device_key():
    from cairnml_agent.commands.worker import VENV_MODE_WARNING
    assert check_execution_mode(use_docker=False, allow_venv=True) == VENV_MODE_WARNING
    assert "device key" in VENV_MODE_WARNING


def test_docker_mode_has_no_warning():
    assert check_execution_mode(use_docker=True, allow_venv=False) is None


def test_the_status_report_names_the_run_mode():
    assert run_mode_for(use_docker=True, allow_venv=False) == "docker"
    assert run_mode_for(use_docker=False, allow_venv=True) == "venv"
    assert run_mode_for(use_docker=False, allow_venv=False) == "host"

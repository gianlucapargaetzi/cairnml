"""The agent runs jobs in Docker unless it is started with --allow-venv."""

import pytest

from cairnml_agent.commands.worker import check_execution_mode


def test_docker_mode_is_accepted():
    check_execution_mode(use_docker=True, allow_venv=False)


def test_venv_mode_is_refused_by_default():
    with pytest.raises(ValueError, match="Docker containers only"):
        check_execution_mode(use_docker=False, allow_venv=False)


def test_venv_mode_is_accepted_only_with_the_flag():
    check_execution_mode(use_docker=False, allow_venv=True)

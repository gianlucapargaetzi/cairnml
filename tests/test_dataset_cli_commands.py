"""The commands the examples and the documentation use must exist: a command dropped by accident fails here, not for a reader."""
import pytest

from cairnml.cli.dataset.__main__ import cli

COMMANDS = ["init", "remote add", "clone", "status", "add", "check", "commit", "doctor", "push", "get", "create", "list", "cache"]


@pytest.mark.parametrize("command", COMMANDS)
def test_the_dataset_command_exists(command, capsys):
    with pytest.raises(SystemExit) as exit_:
        cli(command.split() + ["--help"])
    assert exit_.value.code == 0
    assert "usage:" in capsys.readouterr().out

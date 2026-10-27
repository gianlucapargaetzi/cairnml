import importlib
import sys
from unittest import mock

import pytest

from cairnml.cli import __main__ as cli


def test_a_command_runs_its_tool_with_the_rest_of_the_command_line(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cairnml"])
    seen = {}

    def tool():
        seen["argv"] = list(sys.argv)
        return 0

    with mock.patch("cairnml.cli.enrolment.init_main", tool):
        assert cli.main(["init", "http://localhost", "--force"]) == 0
    assert seen["argv"] == ["cairnml init", "http://localhost", "--force"]


def test_the_exit_status_of_the_tool_is_the_one_returned(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cairnml"])
    with mock.patch("cairnml.cli.enrolment.whoami_main", return_value=1):
        assert cli.main(["whoami"]) == 1


def test_an_unknown_command_is_refused_and_the_commands_are_listed(capsys):
    assert cli.main(["enroll"]) == 2
    err = capsys.readouterr().err
    assert "unknown command 'enroll'" in err
    assert "init" in err and "dataset" in err


def test_no_command_prints_the_commands(capsys):
    assert cli.main([]) == 2
    assert "usage: cairnml <command>" in capsys.readouterr().out


@pytest.mark.parametrize("name", sorted(cli.COMMANDS))
def test_every_command_names_a_tool_that_exists(name):
    module, function, _ = cli.COMMANDS[name]
    assert callable(getattr(importlib.import_module(module), function))

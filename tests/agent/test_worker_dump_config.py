"""Worker.dump_config writes the job's configuration file, and reports only a file it cannot write (issue #115)."""

import json
from types import SimpleNamespace

import pytest
from pyhocon import ConfigFactory

from cairnml_agent.commands.worker import Worker


def stub_worker(session_config=None):
    return SimpleNamespace(_session=SimpleNamespace(config=session_config))


class Broken:
    def as_plain_ordered_dict(self):
        raise RuntimeError("the configuration is broken")


def test_the_file_gets_the_configuration_as_json(tmp_path):
    target = tmp_path / "job.conf"

    assert Worker.dump_config(stub_worker(), str(target), config=ConfigFactory.parse_string("a.b = 1")) is True

    assert json.loads(target.read_text()) == {"a": {"b": 1}}


def test_the_session_configuration_is_dumped_when_no_configuration_is_given(tmp_path):
    target = tmp_path / "agent.conf"
    worker = stub_worker(session_config=ConfigFactory.parse_string("x.y = 2"))

    assert Worker.dump_config(worker, str(target)) is True

    assert json.loads(target.read_text()) == {"x": {"y": 2}}


def test_an_unchanged_file_is_still_reported_as_written(tmp_path):
    target = tmp_path / "job.conf"
    config = ConfigFactory.parse_string("a.b = 1")
    Worker.dump_config(stub_worker(), str(target), config=config)

    assert Worker.dump_config(stub_worker(), str(target), config=config) is True


def test_a_file_that_cannot_be_written_is_reported_with_its_name_and_gives_false(tmp_path, capsys):
    target = tmp_path  # a folder cannot be written as a file

    assert Worker.dump_config(stub_worker(), str(target), config=ConfigFactory.parse_string("a.b = 1")) is False

    assert f"Could not write the configuration file {target}" in capsys.readouterr().out


def test_a_configuration_that_cannot_be_dumped_is_raised_and_no_file_is_written(tmp_path):
    target = tmp_path / "job.conf"

    with pytest.raises(RuntimeError, match="the configuration is broken"):
        Worker.dump_config(stub_worker(), str(target), config=Broken())

    assert not target.exists()

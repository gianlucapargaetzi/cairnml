"""ClearML-era TRAINS_ environment variables are not read; the CAIRNML_ name is the only one."""

import importlib

import pytest

from cairnml.backend_config.environment import EnvEntry


def test_an_env_entry_reads_only_its_cairnml_name(monkeypatch):
    entry = EnvEntry("CAIRNML_TEST_ONLY_NAME")
    monkeypatch.delenv("CAIRNML_TEST_ONLY_NAME", raising=False)
    monkeypatch.setenv("TRAINS_TEST_ONLY_NAME", "old")
    assert not entry.get()


def test_an_env_entry_reads_its_cairnml_name(monkeypatch):
    entry = EnvEntry("CAIRNML_TEST_ONLY_NAME")
    monkeypatch.setenv("CAIRNML_TEST_ONLY_NAME", "new")
    assert entry.get() == "new"


def test_the_legacy_helpers_are_gone():
    environment = importlib.import_module("cairnml.backend_config.environment")
    assert not hasattr(environment, "read_env_with_legacy")
    assert not hasattr(environment, "legacy_name_message")


@pytest.mark.parametrize("module", ["cairnml.config.defs", "cairnml.backend_api.session.defs", "cairnml.backend_config.defs"])
def test_the_definitions_have_no_second_key(module):
    importlib.import_module(module)
    source = importlib.import_module(module).__file__
    with open(source, encoding="utf-8") as handle:
        assert "TRAINS_" not in handle.read()

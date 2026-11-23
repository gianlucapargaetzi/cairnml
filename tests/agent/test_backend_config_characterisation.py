"""What the agent's configuration does today, pinned before the class becomes a subclass of the SDK's (issue #115).

The agent's Config is the copy it has carried since v0.0.6. These tests state each behaviour the agent relies on:
the methods its callers use (put, pop, as_plain_ordered_dict, copy, attribute access into the tree), the way a
missing key is reported (get gives the default or a sentinel, square brackets give None), the files and variables
the configuration is read from, the defaults of the agent's own settings, and the logging level of debug mode.
Passing here is the proof that the replacement is equal in behaviour.
"""

import json
import logging
import os
from os.path import expanduser
from pathlib import Path

import pytest
from pyhocon import ConfigTree

from cairnml_agent.backend_api.config import load
from cairnml_agent.backend_config import defs
from cairnml_agent.backend_config.config import Config

DEFAULTS_SNAPSHOT = Path(__file__).parent / "backend_config_defaults.json"


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """No file or variable of the machine reaches the configuration: only the test's own values do."""
    for name in list(os.environ):
        if name.startswith("CAIRNML_AGENT__"):
            monkeypatch.delenv(name)
    for name in ("CAIRNML_ENV", "CAIRNML_CONFIG_PATH", "CAIRNML_CONFIG_VERBOSE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CAIRNML_CONFIG_FILE", str(tmp_path / "absent.conf"))
    return tmp_path


@pytest.fixture
def logging_restored():
    root = logging.getLogger()
    level = root.level
    yield
    root.setLevel(level)


def new_config(**kwargs):
    kwargs.setdefault("verbose", False)
    return Config(**kwargs)


# ----- files and variables the configuration is read from


def test_the_agent_reads_its_own_three_local_files_and_not_the_sdk_file():
    assert list(defs.LOCAL_CONFIG_FILES) == [
        expanduser("~/trains.conf"),
        expanduser("~/cairnml-agent.conf"),
        expanduser("~/.cairnml/agent/cairnml-agent.conf"),
    ]
    assert expanduser("~/cairnml.conf") not in defs.LOCAL_CONFIG_FILES


def test_the_override_variable_replaces_the_local_files(isolated, monkeypatch):
    override = isolated / "custom.conf"
    override.write_text("x.y = 1\n")
    monkeypatch.setenv("CAIRNML_CONFIG_FILE", str(override))

    config = new_config()
    config.reload()

    assert config.get("x.y") == 1


def test_a_local_file_is_read_when_it_exists(isolated, monkeypatch):
    local = isolated / "local.conf"
    local.write_text("from.file = yes\n")
    monkeypatch.setenv("CAIRNML_CONFIG_FILE", str(local))

    config = new_config()
    assert config.get("from.file", None) is None  # the constructor reads no file
    config.reload()

    assert config.get("from.file") == "yes"


def test_agent_variables_override_the_files_with_their_path_and_lowercased_key(isolated, monkeypatch):
    override = isolated / "custom.conf"
    override.write_text("x.y = 1\napi.http.retries = 1\n")
    monkeypatch.setenv("CAIRNML_CONFIG_FILE", str(override))
    monkeypatch.setenv("CAIRNML_AGENT__x__y", "2")
    monkeypatch.setenv("CAIRNML_AGENT__Api__Http__Retries", "3")

    config = new_config()
    config.reload()

    assert config.get("x.y") == 2
    assert config.get("api.http.retries") == 3


def test_a_variable_outside_the_agent_prefix_is_not_read(isolated, monkeypatch):
    monkeypatch.setenv("CAIRNML__x__y", "9")

    config = new_config()

    assert config.get("x.y", "absent") == "absent"


def test_a_named_environment_is_read_from_the_variable(isolated, monkeypatch):
    monkeypatch.setenv("CAIRNML_ENV", "demo")

    assert new_config().env == "demo"


def test_the_default_environment_is_default(isolated):
    config = new_config()

    assert config.env == "default"
    config.reload()
    assert config.to_dict()["env"] == "default"


def test_an_unknown_environment_is_refused(isolated):
    with pytest.raises(ValueError):
        new_config(env="no-such-environment")


def test_the_constructor_prints_the_environment_when_verbose_is_not_given(isolated, capsys):
    Config()

    assert "Config env:default" in capsys.readouterr().out


def test_a_folder_next_to_a_module_is_read_for_the_default_environment(isolated):
    module = isolated / "module" / "x.py"
    (module.parent / "config" / "default").mkdir(parents=True)
    (module.parent / "config" / "default" / "part.conf").write_text("k = 2\n")

    config = new_config(relative_to=str(module))

    assert config.get("part.k") == 2


def test_a_malformed_file_raises_a_configuration_error_naming_the_file(isolated, monkeypatch):
    bad = isolated / "bad.conf"
    bad.write_text("a = {\n")
    monkeypatch.setenv("CAIRNML_CONFIG_FILE", str(bad))

    config = new_config()
    with pytest.raises(Exception) as raised:
        config.reload()

    assert str(bad) in str(raised.value)
    assert raised.value.file_path == str(bad)


# ----- the defaults of the agent's settings


def agent_defaults():
    config = load()
    values = config.to_dict()
    return {key: values[key] for key in sorted(values) if key != "env"}


def test_the_agent_defaults_are_the_v0_0_6_defaults(isolated):
    """The keys of agent.conf, api.conf, sdk.conf and logging.conf as v0.0.6 loads them (the snapshot)."""
    expected = json.loads(DEFAULTS_SNAPSHOT.read_text())

    assert agent_defaults() == expected


# ----- the methods the agent calls


def test_put_sets_a_nested_key_and_replaces_a_value(isolated):
    config = new_config()

    config.put("x.y.z", "v")
    config.put("x.y.z", "w")

    assert config.get("x.y.z") == "w"
    assert config.to_dict()["x"]["y"]["z"] == "w"


def test_pop_returns_the_value_and_removes_it(isolated):
    config = new_config()
    config.put("a.b", 1)

    assert config.pop("a.b") == 1
    assert config.get("a.b", "gone") == "gone"


def test_pop_of_a_missing_key_gives_the_default(isolated):
    config = new_config()

    assert config.pop("no.such") is None
    assert config.pop("no.such", 5) == 5


def test_as_plain_ordered_dict_is_the_tree_as_plain_dicts(isolated):
    config = new_config()
    config.put("a.b", 1)

    plain = config.as_plain_ordered_dict()

    assert plain == {"a": {"b": 1}}
    assert not isinstance(plain, ConfigTree)
    assert isinstance(plain["a"], dict) and not isinstance(plain["a"], ConfigTree)


def test_to_dict_and_as_json_hold_the_same_content(isolated):
    config = new_config()
    config.put("a.b", "text")

    assert json.loads(config.as_json()) == config.to_dict()


def test_attribute_access_reaches_a_subtree_of_the_tree(isolated):
    config = new_config()
    config.put("agent.translate_ssh", False)

    assert config.agent.translate_ssh is False


def test_copy_gives_a_copy_of_the_tree(isolated):
    config = new_config()
    config.put("a.b", 1)

    copied = config.copy()

    assert isinstance(copied, ConfigTree)
    assert copied["a"]["b"] == 1


def test_an_unknown_attribute_is_an_attribute_error(isolated):
    assert not hasattr(new_config(), "no_such_attribute_xyz")


# ----- the way a missing key is reported


def test_get_of_a_missing_key_gives_the_sentinel_not_an_error(isolated):
    config = new_config()

    assert config.get("no.such.key") is Config._MISSING


def test_get_of_a_missing_key_gives_the_default_that_is_passed(isolated):
    config = new_config()

    assert config.get("no.such", 7) == 7
    assert config.get("no.such", None) is None


def test_get_of_a_present_key_gives_its_value(isolated):
    config = new_config()
    config.put("a.b", 1)

    assert config.get("a.b") == 1
    assert config.get("a.b", 9) == 1


def test_square_brackets_give_none_for_a_missing_key(isolated):
    config = new_config()
    config.put("a.b", 1)

    assert config["no.such"] is None
    assert config["a.b"] == 1


def test_square_brackets_give_none_through_a_scalar(isolated):
    config = new_config()
    config.put("s", "text")

    assert config["s.t"] is None


# ----- overrides


def test_resolve_override_configs_is_none_without_overrides(isolated):
    assert new_config().resolve_override_configs() is None


def test_overrides_win_over_the_files_and_resolve_to_a_tree(isolated, monkeypatch):
    local = isolated / "local.conf"
    local.write_text("x.y = 1\n")
    monkeypatch.setenv("CAIRNML_CONFIG_FILE", str(local))
    config = new_config()

    config.set_overrides({"x": {"y": 5}})

    assert config.get("x.y") == 5
    resolved = config.resolve_override_configs()
    assert isinstance(resolved, ConfigTree)
    assert resolved["x"]["y"] == 5


# ----- debug mode and logging


def test_debug_sets_the_level_of_the_loggers_and_the_root_to_debug(isolated, logging_restored):
    config = new_config()
    config.set_overrides(
        {
            "logging": {
                "version": 1,
                "disable_existing_loggers": False,
                "root": {"level": "INFO", "handlers": []},
                "loggers": {"characterisation": {"level": "WARNING", "handlers": []}},
            }
        }
    )

    assert config.initialize_logging(debug=True) is True

    assert config.get("logging.root.level") == "DEBUG"
    assert config.get("logging.loggers.characterisation.level") == "DEBUG"


def test_without_debug_the_configured_levels_are_kept(isolated, logging_restored):
    config = new_config()
    config.set_overrides(
        {
            "logging": {
                "version": 1,
                "disable_existing_loggers": False,
                "root": {"level": "INFO", "handlers": []},
            }
        }
    )

    config.initialize_logging()

    assert config.get("logging.root.level") == "INFO"


def test_without_a_logging_section_nothing_is_initialised(isolated):
    config = new_config()

    assert config.initialize_logging() is False
    assert config.initialize_logging(debug=True) is False

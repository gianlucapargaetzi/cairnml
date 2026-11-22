import re
from pathlib import Path

import cairnml
from pyhocon import ConfigFactory, ConfigTree

from cairnml.backend_config import Config
from cairnml.cli.config.__main__ import write_config

DEFAULT_SDK = Path(cairnml.__file__).parent / "config" / "default" / "sdk.conf"
DEVICE = "dev_" + "a" * 32


def test_the_written_config_names_the_device_and_holds_no_credentials(tmp_path):
    conf = tmp_path / "cairnml.conf"
    write_config(conf, "https://api.example", "https://app.example", "dev_1")

    text = conf.read_text()
    assert "access_key" not in text
    assert "secret_key" not in text
    assert "credentials {" not in text  # the API credentials block

    from pyhocon import ConfigFactory
    parsed = ConfigFactory.parse_string(text)
    assert parsed.get("api.api_server") == "https://api.example"
    assert parsed.get("api.web_server") == "https://app.example"
    assert parsed.get("api.idp.device_id") == "dev_1"


def written_file(folder):
    conf = folder / "cairnml.conf"
    write_config(conf, "http://cairnml/cairnml/api", "http://cairnml/cairnml", DEVICE)
    return conf


def leaf_keys(tree, prefix=""):
    keys = set()
    for key, value in tree.items():
        if isinstance(value, ConfigTree):
            keys |= leaf_keys(value, f"{prefix}{key}.")
        else:
            keys.add(f"{prefix}{key}")
    return keys


def packaged_sdk_keys():
    return {f"sdk.{key}" for key in leaf_keys(ConfigFactory.parse_file(str(DEFAULT_SDK)))}


def loaded(monkeypatch, conf, defaults_root=Path(cairnml.__file__).parent):
    """The configuration as the SDK loads it: the defaults of the given installed version, then the local file."""
    monkeypatch.setenv("CAIRNML_CONFIG_FILE", str(conf))
    config = Config()
    config.load_relative_to(str(defaults_root / "x.py"))
    return config


def test_the_file_written_by_init_holds_no_copy_of_the_defaults(tmp_path):
    conf = written_file(tmp_path)

    parsed = ConfigFactory.parse_file(str(conf))
    assert not (leaf_keys(parsed) & packaged_sdk_keys())
    assert "sdk" not in parsed
    assert len(conf.read_text().splitlines()) < 40


def test_an_upgrade_changes_the_default_of_a_written_file(tmp_path, monkeypatch):
    conf = written_file(tmp_path)
    newer = tmp_path / "newer" / "config" / "default"
    newer.mkdir(parents=True)
    (newer / "sdk.conf").write_text('{ storage { cache { default_base_dir: "/new/cache" } } }\n')

    config = loaded(monkeypatch, conf, defaults_root=tmp_path / "newer")

    assert config.get("sdk.storage.cache.default_base_dir") == "/new/cache"


def test_packaged_defaults_apply_and_a_hand_written_value_wins(tmp_path, monkeypatch):
    conf = written_file(tmp_path)
    assert loaded(monkeypatch, conf).get("sdk.storage.cache.default_base_dir") == "~/.cairnml/cache"

    conf.write_text(conf.read_text() + 'sdk { storage { cache { default_base_dir: "/mine" } } }\n')
    config = loaded(monkeypatch, conf)

    assert config.get("sdk.storage.cache.default_base_dir") == "/mine"
    assert config.get("api.idp.device_id") == DEVICE


def test_the_header_keeps_the_device_line_the_e2e_suite_and_teardown_read(tmp_path):
    conf = written_file(tmp_path)

    text = conf.read_text()
    assert re.search(r'device_id\s*[:=]\s*"?(dev_[0-9a-f]{32})', text).group(1) == DEVICE
    assert 'device_id: "dev_' in text
    assert "api_server: http://cairnml/cairnml/api" in text
    assert "web_server: http://cairnml/cairnml" in text

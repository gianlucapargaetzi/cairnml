"""The start-up configuration is a short summary; the full dump needs --debug or the config command."""

import logging
from types import SimpleNamespace

from pyhocon import ConfigFactory

from cairnml_agent.backend_api.config import load
from cairnml_agent.session import Session

CONFIG = """
api {
    api_server: "http://cairnml.example:8008"
    credentials { secret_key: "sk-hidden-value" }
}
"""


def stand_in(tmp_path, debug):
    """The attributes print_configuration reads, without an enrolled Session (which needs a token and a server)."""
    conf = tmp_path / "a.conf"
    conf.write_text(CONFIG, encoding="utf-8")
    config = load()
    config.set_overrides(ConfigFactory.parse_string(CONFIG))
    return SimpleNamespace(
        version=Session.version,
        config=config,
        _config_file=str(conf),
        log=logging.getLogger("test-print-configuration"),
        debug_mode=debug,
    )


def test_the_summary_is_printed_without_debug(tmp_path, capsys):
    Session.print_configuration(stand_in(tmp_path, debug=False))
    out = capsys.readouterr().out
    assert len(out.splitlines()) < 10
    assert "Current configuration" not in out
    assert str(tmp_path / "a.conf") in out
    assert "cairnml.example:8008" in out
    assert "--debug" in out and "config" in out


def test_the_full_dump_is_printed_with_debug_and_hides_the_secrets(tmp_path, capsys):
    Session.print_configuration(stand_in(tmp_path, debug=True))
    out = capsys.readouterr().out
    assert "Current configuration" in out
    assert "api.api_server" in out
    assert "****" in out
    assert "sk-hidden-value" not in out


def test_the_config_command_prints_the_full_dump_without_debug(tmp_path, capsys):
    Session.print_configuration(stand_in(tmp_path, debug=False), full=True)
    out = capsys.readouterr().out
    assert "Current configuration" in out
    assert "sk-hidden-value" not in out

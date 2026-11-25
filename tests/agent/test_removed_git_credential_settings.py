"""The settings that gave the agent Git credentials of its own are gone; a configuration that still has them is told so
once, and none of them has an effect (cairnml#194)."""

import pytest
from pyhocon import ConfigFactory

from cairnml_agent.commands import worker as worker_module
from cairnml_agent.helper.repo import REMOVED_ENVIRONMENT_VARIABLES, REMOVED_SETTINGS, removed_credential_settings

OLD_CONFIGURATION = """
agent {
    git_user: "bob"
    git_pass: "hunter2"
    git_host: "github.com"
    git { extra_credentials: [{user: "u", pass: "p", host: "h"}] }
    force_git_ssh_protocol: true
    disable_ssh_mount: true
    translate_ssh: true
}
"""


def test_a_configuration_with_the_removed_settings_names_them():
    config = ConfigFactory.parse_string(OLD_CONFIGURATION)
    assert removed_credential_settings(config, {}) == [
        "agent.git_user",
        "agent.git_pass",
        "agent.git_host",
        "agent.git",
        "agent.force_git_ssh_protocol",
        "agent.disable_ssh_mount",
        "agent.translate_ssh",
    ]


def test_the_removed_environment_variables_are_named_when_set():
    environment = {
        "CAIRNML_AGENT_GIT_USER": "bob",
        "CAIRNML_AGENT_GIT_PASS": "x",
        "HOME": "/h",
        "CAIRNML_AGENT_GIT_HOST": "",
    }
    assert removed_credential_settings(ConfigFactory.parse_string("agent {}"), environment) == [
        "CAIRNML_AGENT_GIT_USER",
        "CAIRNML_AGENT_GIT_PASS",
    ]


def test_empty_and_false_values_are_not_a_credential_and_are_not_named():
    config = ConfigFactory.parse_string('agent { git_user: "", git_pass: "", force_git_ssh_protocol: false }')
    assert removed_credential_settings(config, {}) == []


def test_the_shipped_defaults_do_not_set_any_of_them():
    from pathlib import Path

    text = (
        Path(worker_module.__file__).resolve().parents[1] / "backend_api" / "config" / "default" / "agent.conf"
    ).read_text()
    config = ConfigFactory.parse_string(text)
    assert removed_credential_settings(config, {}) == []


def test_the_warning_names_every_removed_setting_it_found_once_and_no_value(capsys):
    config = ConfigFactory.parse_string(OLD_CONFIGURATION)
    worker = type(
        "W", (), {"_session": type("S", (), {"config": config})(), "log": type("L", (), {"warning": lambda *a: None})()}
    )()
    worker_module.Worker._warn_about_removed_git_credential_settings(worker, environment={})
    out = capsys.readouterr().out
    assert out.count("ignoring") == 1
    assert "agent.git_user" in out and "agent.git_pass" in out
    assert "hunter2" not in out and "bob" not in out


@pytest.mark.parametrize("name", REMOVED_ENVIRONMENT_VARIABLES)
def test_no_code_reads_a_removed_environment_variable(name):
    from pathlib import Path

    root = Path(worker_module.__file__).resolve().parents[1]
    allowed = {"helper/repo.py", "helper/docker_args.py"}
    hits = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if name in path.read_text() and str(path.relative_to(root)) not in allowed
    ]
    assert hits == []


def test_every_removed_setting_is_named_in_the_list():
    assert "agent.git_user" in REMOVED_SETTINGS and "agent.git" in REMOVED_SETTINGS

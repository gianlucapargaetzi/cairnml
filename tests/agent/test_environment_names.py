"""The agent's environment names: one name per setting, and the documented table lists exactly the names the code reads."""

import ast
import importlib.util
import re
from pathlib import Path

import pytest

from cairnml_agent.backend_api.session import defs as session_defs
from cairnml_agent.backend_config import defs as config_defs
from cairnml_agent.backend_config import environment

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "cairnml_agent"
DOCKER = ROOT / "docker"
DOCUMENTATION = ROOT / "docs" / "environment.md"

CAIRNML_NAME = re.compile(r"CAIRNML_[A-Z][A-Z0-9_]*[A-Z0-9]")
# Strings that look like a name but are a prefix built at run time, not a variable.
NOT_VARIABLES = {"CAIRNML_TASK", "CAIRNML_AGENT"}
TABLE_ROW = re.compile(r"^\| `(CAIRNML_[A-Z0-9_]+)` \|", re.MULTILINE)


def _source_files(base, suffixes):
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if "_vendor" in path.parts or "__pycache__" in path.parts:
            continue
        yield path


def test_no_trains_name_is_left_in_the_agent_or_the_docker_scripts():
    offenders = []
    for path in _source_files(PACKAGE, {".py", ".conf", ".txt", ".yml", ".yaml", ".json"}):
        if "TRAINS_" in path.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(str(path.relative_to(ROOT)))
    for path in _source_files(DOCKER, {".sh", ".yml", ".yaml", ".conf", ".txt", ".md", ""}):
        if "TRAINS_" in path.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_the_backward_compatibility_mirror_is_gone():
    assert not hasattr(environment, "backward_compatibility_support")


@pytest.mark.parametrize(
    "entry, name, value, expected",
    [
        pytest.param(
            session_defs.ENV_DISABLE_VAULT_SUPPORT,
            "CAIRNML_DISABLE_VAULT_SUPPORT",
            "true",
            True,
            id="disable-vault-support",
        ),
        pytest.param(
            session_defs.ENV_ENABLE_ENV_CONFIG_SECTION,
            "CAIRNML_ENABLE_ENV_CONFIG_SECTION",
            "true",
            True,
            id="enable-env-config-section",
        ),
        pytest.param(
            session_defs.ENV_ENABLE_FILES_CONFIG_SECTION,
            "CAIRNML_ENABLE_FILES_CONFIG_SECTION",
            "true",
            True,
            id="enable-files-config-section",
        ),
        pytest.param(
            session_defs.ENV_FORCE_MAX_API_VERSION,
            "CAIRNML_FORCE_MAX_API_VERSION",
            "2.5",
            "2.5",
            id="force-max-api-version",
        ),
        pytest.param(config_defs.ENV_CONFIG_VERBOSE, "CAIRNML_CONFIG_VERBOSE", "1", True, id="config-verbose"),
    ],
)
def test_the_shared_knob_is_read_under_its_sdk_name(monkeypatch, entry, name, value, expected):
    monkeypatch.setenv(name, value)

    assert entry.get() == expected


@pytest.mark.parametrize(
    "entry, name, value",
    [
        pytest.param(
            session_defs.ENV_DISABLE_VAULT_SUPPORT, "CAIRNML_DISABLE_VAULT_SUPPORT", "true", id="disable-vault-support"
        ),
        pytest.param(
            session_defs.ENV_ENABLE_ENV_CONFIG_SECTION,
            "CAIRNML_ENABLE_ENV_CONFIG_SECTION",
            "true",
            id="enable-env-config-section",
        ),
        pytest.param(
            session_defs.ENV_ENABLE_FILES_CONFIG_SECTION,
            "CAIRNML_ENABLE_FILES_CONFIG_SECTION",
            "true",
            id="enable-files-config-section",
        ),
        pytest.param(
            session_defs.ENV_FORCE_MAX_API_VERSION, "CAIRNML_FORCE_MAX_API_VERSION", "2.5", id="force-max-api-version"
        ),
        pytest.param(config_defs.ENV_CONFIG_VERBOSE, "CAIRNML_CONFIG_VERBOSE", "1", id="config-verbose"),
    ],
)
def test_the_old_agent_prefixed_knob_name_is_ignored(monkeypatch, entry, name, value):
    old_name = name.replace("CAIRNML_", "CAIRNML_AGENT_", 1)
    monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(old_name, value)

    assert not entry.get()


# SDK modules the agent imports and runs for it: the names they read are read on the agent's behalf.
READ_FOR_THE_AGENT_BY_SDK = ("cairnml.idp.browser_login",)


def _names_in_sdk_modules():
    names = set()
    for module in READ_FOR_THE_AGENT_BY_SDK:
        origin = importlib.util.find_spec(module).origin
        names.update(CAIRNML_NAME.findall(Path(origin).read_text(encoding="utf-8")))
    return names


def _names_in_code():
    names = _names_in_sdk_modules()
    for path in _source_files(PACKAGE, {".py"}):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.update(CAIRNML_NAME.findall(node.value))
            # keyword arguments named for variables, such as dict(CAIRNML_PIP_VER=...) for the bootstrap
            if isinstance(node, ast.keyword) and node.arg:
                names.update(CAIRNML_NAME.findall(node.arg))
    for path in _source_files(PACKAGE, {".conf"}):
        names.update(CAIRNML_NAME.findall(path.read_text(encoding="utf-8")))
    return names - NOT_VARIABLES


def _names_in_table():
    return set(TABLE_ROW.findall(DOCUMENTATION.read_text(encoding="utf-8")))


def test_every_name_the_code_reads_is_in_the_documented_table():
    missing = sorted(_names_in_code() - _names_in_table())

    assert missing == []


def test_every_name_in_the_documented_table_is_read_by_the_code():
    unknown = sorted(_names_in_table() - _names_in_code())

    assert unknown == []


def test_no_removed_access_key_name_is_left():
    removed = ("CAIRNML_API_ACCESS_KEY", "CAIRNML_API_SECRET_KEY")
    files = [
        *_source_files(PACKAGE, {".py", ".conf"}),
        *_source_files(DOCKER, {".yml", ".yaml", ".conf"}),
        DOCUMENTATION,
    ]
    offenders = sorted(
        {
            str(path.relative_to(ROOT))
            for path in files
            if any(name in path.read_text(encoding="utf-8") for name in removed)
        }
    )

    assert offenders == []


def test_the_ssh_setup_command_has_no_accept_env_and_no_empty_step():
    from cairnml_agent.glue.k8s import K8sIntegration

    command = K8sIntegration.get_ssh_server_bash(10022)
    segments = command.split(" ; ")

    assert "/usr/sbin/sshd -p 10022" in command
    assert "PermitRootLogin yes" in command
    assert "AcceptEnv" not in command
    assert all(segment.strip() for segment in segments)

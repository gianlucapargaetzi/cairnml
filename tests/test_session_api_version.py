"""
How the SDK picks a definition of each action by version, and what it does when the server is older than the
definition a request is sent at (#119). A request carries the version of the definition it implements; a newer server
serves it silently, an older one refuses it before anything is sent.
"""

import re
from pathlib import Path

import pytest

import cairnml
from cairnml.backend_api.services import API_LEVEL, UnsupportedApiVersion, projects, queues, resolve
from cairnml.backend_api.session import Session
from cairnml.backend_api.session.defs import newest_version


@pytest.fixture
def server_level(monkeypatch):
    """Sets the API level the server reported, as the token's api_version does after sign-in."""

    def set_level(level):
        monkeypatch.setattr(Session, "max_api_version", level)

    return set_level


def _session():
    return Session.__new__(Session)


def test_the_sdk_level_is_the_generated_level():
    assert Session.api_version == API_LEVEL
    assert Session.max_api_version == API_LEVEL


def test_the_newest_definition_is_picked_by_version():
    assert newest_version("queues", "get_all") == queues.GetAllRequest._version
    assert newest_version("projects", "get_all_ex") == projects.GetAllExRequest._version == "2.41"


def test_an_older_definition_is_picked_for_an_older_version():
    request_cls, _ = resolve("projects", "get_all_ex", "2.30")
    assert request_cls._version == "2.27"
    request_cls, _ = resolve("projects", "get_all_ex", "2.41")
    assert request_cls._version == "2.41"


def test_a_version_older_than_every_definition_is_refused_by_the_resolver():
    with pytest.raises(UnsupportedApiVersion):
        resolve("projects", "get_all_ex", "2.1")


def test_a_request_is_sent_when_the_server_is_as_new_as_its_definition(server_level):
    server_level("2.41")
    _session()._check_server_serves("projects", "get_all_ex", "2.41")


def test_a_request_is_sent_to_a_newer_server_without_a_word(server_level, caplog):
    server_level("2.50")
    with caplog.at_level("WARNING"):
        _session()._check_server_serves("projects", "get_all_ex", "2.41")
    assert caplog.records == []


def test_a_request_to_an_older_server_is_refused_naming_both_versions(server_level):
    server_level("2.40")
    with pytest.raises(UnsupportedApiVersion) as refused:
        _session()._check_server_serves("projects", "get_all_ex", "2.41")
    assert "projects.get_all_ex" in str(refused.value)
    assert "2.41" in str(refused.value) and "2.40" in str(refused.value)


def test_a_request_older_than_the_server_is_sent(server_level):
    server_level("2.40")
    _session()._check_server_serves("projects", "get_all_ex", "2.27")


def test_a_request_without_a_version_is_not_checked(server_level):
    server_level("1.0")
    _session()._check_server_serves("projects", "get_all_ex", None)


def test_a_refused_request_is_not_sent(server_level):
    # the check comes before any transport is touched, so an older server is refused without a request being made
    server_level("2.40")
    session = _session()
    session._offline_mode = False
    with pytest.raises(UnsupportedApiVersion):
        session._send_request(service="projects", action="get_all_ex", version="2.41", json={})


def test_no_hand_written_module_carries_a_version_literal():
    """The hand-written SDK picks definitions through the resolver: no API_VERSION and no version literal passed as a version."""
    root = Path(cairnml.__file__).parent
    generated = root / "backend_api" / "services"
    # the packages that talk to the server (the automation controller's own pipeline versions are not API versions)
    talking = [root / name for name in ("backend_api", "backend_interface", "datasets", "storage", "idp")]
    offenders = []
    for path in (p for folder in talking for p in folder.rglob("*.py")):
        if generated in path.parents:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if "API_VERSION" in line or re.search(r"""(?<![\w-])version\s*[:=]\s*["']\d+\.\d+["']""", line):
                offenders.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    assert offenders == []

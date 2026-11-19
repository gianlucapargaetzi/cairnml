"""
How the SDK picks a definition of each action by version, which version it puts on the wire, and what it does when
the server is older than the definition (#119).

Every request is sent at ONE negotiated level: the lower of the API level the client was generated for (API_LEVEL) and
the level the server reported. It is not the version label of the action's definition. The server serves a request at
version V with the newest definition block at or below V and decides some behaviour on the version the client asked for
(model metadata is blanked to [] for any request at 2.16 or lower), so an action unchanged since 2.1, such as
models.get_by_id, must not make the client ask for 2.1. A newer server serves a request silently, an older one refuses
a definition it cannot read before anything is sent.
"""

import re
from pathlib import Path

import pytest

import cairnml
from cairnml.backend_api.services import API_LEVEL, DEFINITIONS, UnsupportedApiVersion, models, projects, resolve
from cairnml.backend_api.session import Session


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


# service, action, label of the newest definition, level of the server, expected level on the wire, label of the
# definition the server serves at that level. The labels below the level are the point: they used to be sent as they were.
LEVELS = [
    ("models", "get_by_id", "2.1", API_LEVEL, API_LEVEL, "2.1"),
    ("devices", "exchange", "2.8", API_LEVEL, API_LEVEL, "2.8"),
    ("queues", "get_all", "2.21", API_LEVEL, API_LEVEL, "2.21"),
    ("projects", "get_all_ex", "2.41", API_LEVEL, API_LEVEL, "2.41"),
    # a newer server than the SDK: the SDK still asks for its own level
    ("models", "get_by_id", "2.1", "2.60", API_LEVEL, "2.1"),
    # an older server: the request goes at the server's level, and the definition served there is an older one
    ("models", "get_by_id", "2.1", "2.30", "2.30", "2.1"),
    ("queues", "get_all", "2.21", "2.25", "2.25", "2.21"),
    ("projects", "get_all_ex", "2.41", "2.30", "2.30", "2.27"),
]


class _Answer:
    status_code = 200
    text = ""

    def json(self):
        meta = {"id": "i", "trx": "t", "result_code": 200, "result_subcode": 0, "result_msg": "OK", "error_stack": ""}
        return {"meta": meta, "data": {}}


def _recording_session():
    """A session that records the version of every request it would send, without a server."""
    session = _session()
    session._offline_mode = False
    session._logger = None
    session.calls = []

    def send_request(**kwargs):
        session.calls.append(kwargs)
        return _Answer()

    session.send_request = send_request
    return session


@pytest.mark.parametrize("service,action,newest,server,sent_at,served", LEVELS)
def test_a_request_is_sent_at_the_negotiated_level_not_at_the_label_of_its_definition(
    server_level, service, action, newest, server, sent_at, served
):
    server_level(server)
    assert DEFINITIONS[service][action][-1][0] == newest
    assert Session.request_version(service, action) == sent_at
    request_cls, _ = resolve(service, action, sent_at)
    assert request_cls._version == served


def test_models_get_by_id_is_sent_above_the_level_that_blanks_the_model_metadata():
    """The failure that this rule fixes: the definition is labelled 2.1, the server answers a request at 2.16 or lower
    with metadata [] and the SDK expects a dict."""
    session = _recording_session()

    session.send(models.GetByIdRequest(model="m1"))

    (call,) = session.calls
    assert (call["service"], call["action"]) == ("models", "get_by_id")
    assert models.GetByIdRequest._version == "2.1"
    assert call["version"] == API_LEVEL


def test_a_request_to_an_older_server_is_sent_at_the_servers_level(server_level):
    server_level("2.30")
    session = _recording_session()
    request_cls, _ = resolve("projects", "get_all_ex", "2.30")
    assert request_cls._version == "2.27"

    session.send(request_cls())

    (call,) = session.calls
    assert call["version"] == "2.30"


def test_a_definition_newer_than_the_server_is_refused_before_it_is_sent(server_level):
    server_level("2.30")
    session = _recording_session()

    with pytest.raises(UnsupportedApiVersion) as refused:
        session.send(projects.GetAllExRequest())

    assert "projects.get_all_ex" in str(refused.value)
    assert session.calls == []


def test_a_level_below_the_oldest_definition_of_an_action_is_refused_locally(server_level):
    server_level("2.1")
    session = _recording_session()

    with pytest.raises(UnsupportedApiVersion):
        Session.request_version("projects", "get_all_ex")
    with pytest.raises(UnsupportedApiVersion):
        session.send(projects.GetAllExRequest())

    assert session.calls == []


def test_the_level_is_never_below_the_level_of_the_generated_client_for_any_action():
    """No generated action is sent below API_LEVEL while the server is as new as the client."""
    below = [
        f"{service}.{action}"
        for service, actions in DEFINITIONS.items()
        for action in actions
        if Session.request_version(service, action) != API_LEVEL
    ]
    assert below == []


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

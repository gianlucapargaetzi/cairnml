"""Every request the dataset module makes is sent at the negotiated API level (issue #118; the level, not the label of
the generated definition, since the server decides some behaviour on the version a client asks for), and no operation
takes or spells a version of its own."""

import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnml.backend_api.services import API_LEVEL, resolve
from cairnml.datasets import clone as clone_module
from cairnml.datasets import credentials

MODULE_FILES = [
    Path(credentials.__file__),
    Path(clone_module.__file__),
]

# A data object that satisfies every operation's reading of the answer.
ANSWER = {
    "endpoint": "https://s3.example",
    "region": "eu",
    "bucket": "bucket",
    "prefix": "dataset/c1/acme/cars",
    "access_key": "AK",
    "secret_key": "SK",
    "session_token": "ST",
    "expires_at": "2030-01-01T00:00:00Z",
    "token": None,
    "repository": {},
    "repositories": [],
    "credentials": [],
}


class RecordingSession:
    def __init__(self):
        self.calls = []

    def send_request(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(ok=True, text="", json=lambda: {"data": ANSWER})


def _operations(session):
    """Each operation of the module, called once with the recording session: (name, call, service, action)."""
    return [
        ("fetch_storage_keys", lambda: credentials.fetch_storage_keys("acme/cars", "read", session=session),
         "datachunks", "dataset_credentials"),
        ("fetch_person_git_token", lambda: credentials.fetch_person_git_token("https://github.com/a/b", "read", session=session),
         "repositories", "git_token"),
        ("add_repository", lambda: credentials.add_repository("https://github.com/a/b", session=session),
         "repositories", "add"),
        ("register_dataset", lambda: credentials.register_dataset("https://github.com/a/b", session=session),
         "repositories", "add"),
        ("assign_credential", lambda: credentials.assign_credential("https://github.com/a/b", none_needed=True, session=session),
         "repositories", "assign"),
        ("unassign_credential", lambda: credentials.unassign_credential("https://github.com/a/b", session=session),
         "repositories", "unassign"),
        ("check_repository", lambda: credentials.check_repository("https://github.com/a/b", session=session),
         "repositories", "check"),
        ("delete_repository", lambda: credentials.delete_repository("https://github.com/a/b", session=session),
         "repositories", "delete"),
        ("set_repository_shared", lambda: credentials.set_repository_shared("https://github.com/a/b", True, none_needed=True, session=session),
         "repositories", "set_shared"),
        ("link_repository", lambda: credentials.link_repository("https://github.com/a/b", "proj", session=session),
         "repositories", "link"),
        ("unlink_repository", lambda: credentials.unlink_repository("https://github.com/a/b", "proj", session=session),
         "repositories", "unlink"),
        ("resolve_dataset", lambda: credentials.resolve_dataset("https://github.com/a/b", session=session),
         "repositories", "find"),
        ("list_credentials", lambda: credentials.list_credentials(session=session),
         "user_access", "get_all"),
        ("fetch_repository_list", lambda: credentials.fetch_repository_list(session=session),
         "repositories", "get_all"),
        ("clone_answer", lambda: clone_module._clone_answer("https://github.com/a/b", None, session=session),
         "repositories", "clone"),
    ]


@pytest.mark.parametrize("name,service,action", [
    ("fetch_storage_keys", "datachunks", "dataset_credentials"),
    ("fetch_person_git_token", "repositories", "git_token"),
    ("add_repository", "repositories", "add"),
    ("assign_credential", "repositories", "assign"),
    ("unassign_credential", "repositories", "unassign"),
    ("check_repository", "repositories", "check"),
    ("delete_repository", "repositories", "delete"),
    ("set_repository_shared", "repositories", "set_shared"),
    ("link_repository", "repositories", "link"),
    ("unlink_repository", "repositories", "unlink"),
    ("resolve_dataset", "repositories", "find"),
    ("list_credentials", "user_access", "get_all"),
    ("fetch_repository_list", "repositories", "get_all"),
    ("clone_answer", "repositories", "clone"),
])
def test_a_request_is_sent_at_the_negotiated_level(name, service, action):
    session = RecordingSession()
    operation = {op[0]: op for op in _operations(session)}[name]

    operation[1]()

    resolve(service, action, API_LEVEL)  # the action has a definition at the level
    assert len(session.calls) == 1, session.calls
    call = session.calls[0]
    assert (call["service"], call["action"]) == (service, action)
    assert call["version"] == API_LEVEL


def test_the_device_git_token_is_sent_at_the_negotiated_level(monkeypatch):
    monkeypatch.setenv("CAIRNML_JOB_TOKEN", "job-token")
    session = RecordingSession()

    credentials.fetch_git_token("https://github.com/a/b", "task-1", session=session)

    resolve("devices", "git_token", API_LEVEL)
    assert len(session.calls) == 1
    call = session.calls[0]
    assert (call["service"], call["action"]) == ("devices", "git_token")
    assert call["version"] == API_LEVEL


@pytest.mark.parametrize("function", [
    credentials.fetch_storage_keys,
    credentials.running_as_job,
    credentials.fetch_git_token,
    credentials.fetch_person_git_token,
    credentials.add_repository,
    credentials.register_dataset,
    credentials.assign_credential,
    credentials.unassign_credential,
    credentials.check_repository,
    credentials.delete_repository,
    credentials.set_repository_shared,
    credentials.link_repository,
    credentials.unlink_repository,
    credentials.resolve_dataset,
    credentials.list_credentials,
    credentials.fetch_repository_list,
    clone_module.clone_dataset,
    clone_module._clone_answer,
])
def test_no_operation_takes_a_version(function):
    assert "version" not in inspect.signature(function).parameters


@pytest.mark.parametrize("path", MODULE_FILES, ids=lambda path: path.name)
def test_the_module_spells_no_version_string(path):
    source = path.read_text(encoding="utf-8")
    assert "API_VERSION" not in source
    assert not re.search(r"[\"']\d+\.\d+[\"']", source), "a version string literal in the module"

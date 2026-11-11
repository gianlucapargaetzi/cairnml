"""cairnml dataset list and cairnml repository list: what the server answers, as a table or as it is (--json)."""
import json

import pytest

from cairnml.cli.dataset import __main__ as dataset_cli
from cairnml.cli.repository import __main__ as repository_cli
from cairnml.datasets import DatasetError, credentials

DATASETS = {"datasets": [
    {"id": "d1", "name": "cars", "reference": "cars", "kind": "repo", "visibility": "user", "credential": "a1", "state": "ok"},
    {"id": "d2", "name": "faces", "reference": "faces", "kind": "repo", "visibility": "Public", "credential": None,
     "state": "no_access", "state_message": "no access to dataset faces"},
]}
REPOSITORIES = [
    {"host": "github.com", "path": "acme/code", "bindings": [
        {"project": "p1", "project_name": "examples", "owner_name": "alice", "state": "assigned", "credential": "a1", "message": ""},
        {"project": "p2", "project_name": "other", "owner_name": "bob", "state": "unreachable", "credential": None,
         "message": "The repository is private and no credential is assigned (no company GitHub App is set up)."},
    ]},
    {"host": "github.com", "path": "acme/open", "bindings": [
        {"project": None, "owner": "u9", "state": "anonymous", "credential": None, "message": ""}]},
]


def test_dataset_list_prints_a_table_and_the_reason_of_a_dataset_with_no_access(monkeypatch, capsys):
    monkeypatch.setattr(credentials, "fetch_dataset_list", lambda: DATASETS)
    assert dataset_cli.cli(["list"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == ["ID", "NAME", "KIND", "VISIBILITY", "CREDENTIAL", "STATE"]
    assert out.splitlines()[1].split() == ["d1", "cars", "repo", "user", "a1", "ok"]
    assert out.splitlines()[2].split() == ["d2", "faces", "repo", "Public", "no", "access"]
    assert "faces: no access to dataset faces" in out


def test_dataset_list_json_is_what_the_server_answered(monkeypatch, capsys):
    monkeypatch.setattr(credentials, "fetch_dataset_list", lambda: DATASETS)
    assert dataset_cli.cli(["list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == DATASETS["datasets"]


def test_dataset_list_says_what_to_do_when_there_is_none(monkeypatch, capsys):
    monkeypatch.setattr(credentials, "fetch_dataset_list", lambda: {"datasets": []})
    assert dataset_cli.cli(["list"]) == 0
    assert "No datasets yet" in capsys.readouterr().out


def test_dataset_list_reports_a_server_that_cannot_be_asked(monkeypatch, capsys):
    def refuse():
        raise DatasetError("the server could not be asked for the dataset list (no login)")

    monkeypatch.setattr(credentials, "fetch_dataset_list", refuse)
    assert dataset_cli.cli(["list"]) == 1
    assert "no login" in capsys.readouterr().err


def test_repository_list_has_a_line_for_each_project_and_explains_what_cannot_be_read(monkeypatch, capsys):
    monkeypatch.setattr(credentials, "fetch_repository_list", lambda: REPOSITORIES)
    assert repository_cli.list_main() == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].split() == ["REPOSITORY", "PROJECT", "OWNER", "READ", "AS", "CREDENTIAL"]
    assert lines[1].split() == ["github.com/acme/code", "examples", "alice", "credential", "a1"]
    assert lines[2].split()[:5] == ["github.com/acme/code", "other", "bob", "not", "readable"]
    assert lines[3].split()[:5] == ["github.com/acme/open", "(no", "project)", "u9", "public"]
    assert "github.com/acme/code (other): The repository is private and no credential is assigned" in out


def test_repository_list_json_is_what_the_server_answered(monkeypatch, capsys):
    monkeypatch.setattr(credentials, "fetch_repository_list", lambda: REPOSITORIES)
    assert repository_cli.list_main(as_json=True) == 0
    assert json.loads(capsys.readouterr().out) == REPOSITORIES


def test_repository_list_says_when_a_run_has_not_used_one_yet(monkeypatch, capsys):
    monkeypatch.setattr(credentials, "fetch_repository_list", lambda: [])
    assert repository_cli.list_main() == 0
    assert "No repositories yet" in capsys.readouterr().out


def test_repository_list_reports_a_server_that_cannot_be_asked(monkeypatch, capsys):
    def refuse():
        raise DatasetError("the server could not be asked (no login)")

    monkeypatch.setattr(credentials, "fetch_repository_list", refuse)
    assert repository_cli.list_main() == 1
    assert "no login" in capsys.readouterr().err


def test_the_commands_are_listed_by_cairnml(capsys):
    from cairnml.cli import __main__ as top

    assert "repository" in top.COMMANDS
    assert top.main([]) == 2
    assert "repository" in capsys.readouterr().out

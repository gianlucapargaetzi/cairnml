"""cairnml repository: list, add, assign, unassign, check, delete, link, share (and cairnml dataset list)."""
import json

import pytest

from cairnml.cli.dataset import __main__ as dataset_cli
from cairnml.cli.repository import __main__ as cli
from cairnml.datasets import DatasetError, credentials

ROWS = [
    {"host": "github.com", "path": "acme/code", "roles": ["code"], "owner_name": "Ann", "shared": False,
     "access": {"state": "assigned", "message": "Read with mine"},
     "assignments": [{"mine": True, "credential_name": "mine"}, {"mine": False, "credential_name": "theirs"}]},
    {"host": "github.com", "path": "acme/cars", "roles": ["dataset"], "owner_name": "Company", "shared": True,
     "access": {"state": "unreachable", "message": "The repository is private and no credential is assigned"}, "assignments": []},
]


def test_list_shows_a_line_for_each_repository_and_explains_what_cannot_be_read(monkeypatch, capsys):
    monkeypatch.setattr(credentials, "fetch_repository_list", lambda: ROWS)
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].split() == ["REPOSITORY", "TYPE", "OWNER", "READ", "AS", "YOUR", "CREDENTIAL"]
    assert lines[1].split() == ["github.com/acme/code", "code", "Ann", "credential", "mine"]
    assert lines[2].split()[:5] == ["github.com/acme/cars", "dataset", "shared", "not", "readable"]
    assert "github.com/acme/cars: The repository is private" in out


def test_list_can_be_limited_to_a_type_and_printed_as_json(monkeypatch, capsys):
    monkeypatch.setattr(credentials, "fetch_repository_list", lambda: ROWS)
    assert cli.main(["list", "--type", "dataset", "--json"]) == 0
    assert [r["path"] for r in json.loads(capsys.readouterr().out)] == ["acme/cars"]


def test_dataset_list_is_the_repository_list_of_datasets(monkeypatch, capsys):
    monkeypatch.setattr(credentials, "fetch_repository_list", lambda: ROWS)
    assert dataset_cli.cli(["list"]) == 0
    out = capsys.readouterr().out
    assert "acme/cars" in out and "acme/code" not in out


def test_list_says_what_to_do_when_there_is_none_and_reports_a_server_that_cannot_be_asked(monkeypatch, capsys):
    monkeypatch.setattr(credentials, "fetch_repository_list", lambda: [])
    assert cli.main(["list"]) == 0 and "No repositories yet" in capsys.readouterr().out

    def refuse():
        raise DatasetError("the server could not be asked (no login)")

    monkeypatch.setattr(credentials, "fetch_repository_list", refuse)
    assert cli.main(["list"]) == 1 and "no login" in capsys.readouterr().err


@pytest.fixture
def calls(monkeypatch):
    seen = []
    row = {"host": "github.com", "path": "acme/cars", "access": {"state": "assigned", "message": "Read with mine"}}

    def record(name):
        def call(*args, **kwargs):
            seen.append((name, args, kwargs))
            return row
        return call

    for name in ("add_repository", "assign_credential", "unassign_credential", "check_repository", "set_repository_shared",
                 "link_repository", "unlink_repository", "delete_repository"):
        monkeypatch.setattr(credentials, name, record(name))
    return seen


def test_add_lists_a_repository_with_its_type_project_credential_and_sharing(calls, capsys):
    assert cli.main(["add", "https://github.com/acme/cars", "--type", "registry", "--project", "p1", "--credential", "c1",
                     "--shared"]) == 0
    assert calls == [("add_repository", ("https://github.com/acme/cars", "registry", "c1", False, True, "p1"), {})]
    assert "github.com/acme/cars: credential" in capsys.readouterr().out


def test_assign_takes_a_credential_or_none_needed_and_nothing_else(calls, capsys):
    assert cli.main(["assign", "acme/cars", "c1"]) == 0
    assert cli.main(["assign", "acme/cars", "--none-needed"]) == 0
    assert calls[0] == ("assign_credential", ("acme/cars", "c1", False, "dataset"), {})
    assert calls[1] == ("assign_credential", ("acme/cars", None, True, "dataset"), {})
    assert cli.main(["assign", "acme/cars"]) == 2 and cli.main(["assign", "acme/cars", "c1", "--none-needed"]) == 2


def test_the_other_commands_call_the_server_once_each(calls):
    for argv in (["unassign", "r"], ["check", "r"], ["delete", "r"], ["share", "r"], ["unshare", "r"],
                 ["link", "r", "p1"], ["unlink", "r", "p1"]):
        assert cli.main(argv) == 0
    assert [c[0] for c in calls] == ["unassign_credential", "check_repository", "delete_repository", "set_repository_shared",
                                     "set_repository_shared", "link_repository", "unlink_repository"]
    assert calls[3][1] == ("r", True, None, False) and calls[4][1] == ("r", False) and calls[5][1] == ("r", "p1")


def test_a_refusal_of_the_server_is_an_error_line_and_exit_code_one(monkeypatch, capsys):
    def refuse(*args, **kwargs):
        raise DatasetError("the server refused the credential: not yours")

    monkeypatch.setattr(credentials, "assign_credential", refuse)
    assert cli.main(["assign", "acme/cars", "c1"]) == 1
    assert "not yours" in capsys.readouterr().err


def test_the_commands_are_listed_by_cairnml(capsys):
    from cairnml.cli import __main__ as top

    assert "repository" in top.COMMANDS and top.main([]) == 2
    assert "repository" in capsys.readouterr().out


def test_a_refusal_shows_the_servers_reason_not_the_json_envelope():
    from types import SimpleNamespace

    from cairnml.datasets.credentials import _reason

    envelope = '{"meta": {"result_msg": "Validation error (the GitHub App x is not installed on a/b)"}, "data": {}}'
    response = SimpleNamespace(text=envelope, json=lambda: __import__("json").loads(envelope))
    assert _reason(response) == "the GitHub App x is not installed on a/b"
    plain = SimpleNamespace(text=" gateway down ", json=lambda: (_ for _ in ()).throw(ValueError()))
    assert _reason(plain) == "gateway down"

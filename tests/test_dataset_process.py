"""Running git, dvc and gh: a time limit on every command, the token out of the command line, and pull request
problems that are reported instead of swallowed."""

import base64
import os
import stat
import sys

import pytest

from cairnml.datasets import DatasetRepo, process
from cairnml.datasets.gitutil import Git, _env


def test_a_command_that_does_not_finish_ends_with_an_error_that_names_the_limit(monkeypatch):
    monkeypatch.setenv(process.COMMAND_TIMEOUT_ENV, "0.3")
    with pytest.raises(process.CommandTimeout) as caught:
        process.run([sys.executable, "-c", "import time; time.sleep(30)"])
    assert process.COMMAND_TIMEOUT_ENV in str(caught.value)


def test_zero_means_no_limit_and_a_bad_value_is_refused(monkeypatch):
    monkeypatch.setenv(process.COMMAND_TIMEOUT_ENV, "0")
    assert process.command_timeout() is None
    monkeypatch.setenv(process.COMMAND_TIMEOUT_ENV, "soon")
    with pytest.raises(process.DatasetError, match="number of seconds"):
        process.command_timeout()


def test_data_transfers_have_no_limit_unless_one_is_set(monkeypatch):
    monkeypatch.delenv(process.TRANSFER_TIMEOUT_ENV, raising=False)
    assert process.transfer_timeout() is None
    monkeypatch.setenv(process.TRANSFER_TIMEOUT_ENV, "90")
    assert process.transfer_timeout() == 90.0


def test_the_token_is_in_the_environment_of_the_command_and_not_on_its_command_line(tmp_path, monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"], seen["env"] = list(args), kwargs["env"]

        class Done:
            returncode, stdout, stderr = 0, "", ""

        return Done()

    monkeypatch.setattr(process, "run", fake_run)
    Git(tmp_path, token="s3cret-token").run("status")
    assert not any("s3cret" in part or "Authorization" in part for part in seen["args"])
    value = seen["env"]["GIT_CONFIG_VALUE_0"]
    assert seen["env"]["GIT_CONFIG_KEY_0"] == "http.extraheader"
    assert base64.b64decode(value.rsplit(" ", 1)[1]).decode() == "x-access-token:s3cret-token"


def test_the_token_is_added_after_git_settings_that_are_already_in_the_environment(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "kept")
    env = _env("tok")
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_VALUE_0"] == "kept" and env["GIT_CONFIG_KEY_1"] == "http.extraheader"


def test_without_a_token_git_gets_no_extra_settings(monkeypatch):
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    assert "GIT_CONFIG_COUNT" not in _env(None)


def _fake_gh(tmp_path, monkeypatch, create=(0, "https://github.com/acme/cars/pull/7", ""), merge=(0, "", "")):
    """A gh on PATH that answers `pr create` and `pr merge` with the given (exit code, stdout, stderr)."""
    script = tmp_path / "bin" / "gh"
    script.parent.mkdir()
    script.write_text(
        "#!/bin/sh\n"
        f'if [ "$2" = create ]; then echo "{create[1]}"; echo "{create[2]}" >&2; exit {create[0]}; fi\n'
        f'if [ "$2" = merge ]; then echo "{merge[1]}"; echo "{merge[2]}" >&2; exit {merge[0]}; fi\n'
        "exit 2\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{script.parent}{os.pathsep}{os.environ['PATH']}")


def test_a_pull_request_with_auto_merge_is_reported_with_its_url(tmp_path, monkeypatch):
    _fake_gh(tmp_path, monkeypatch)
    result = DatasetRepo(tmp_path)._open_pull_request("incoming/abc", "main")
    assert result == {"pull_request": "https://github.com/acme/cars/pull/7", "auto_merge": True, "problem": None}


def test_a_pull_request_that_could_not_be_opened_says_why(tmp_path, monkeypatch):
    _fake_gh(tmp_path, monkeypatch, create=(1, "", "GraphQL: Resource not accessible by personal access token"))
    result = DatasetRepo(tmp_path)._open_pull_request("incoming/abc", "main")
    assert result["pull_request"] is None and result["auto_merge"] is None
    assert "Resource not accessible" in result["problem"]


def test_a_pull_request_whose_auto_merge_failed_is_still_returned_with_the_reason(tmp_path, monkeypatch):
    _fake_gh(tmp_path, monkeypatch, merge=(1, "", "auto-merge is not allowed for this repository"))
    result = DatasetRepo(tmp_path)._open_pull_request("incoming/abc", "main")
    assert result["pull_request"] == "https://github.com/acme/cars/pull/7"
    assert result["auto_merge"] is False and "not allowed" in result["problem"]


def test_a_missing_gh_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # no gh here
    result = DatasetRepo(tmp_path)._open_pull_request("incoming/abc", "main")
    assert result["pull_request"] is None and "gh" in result["problem"]

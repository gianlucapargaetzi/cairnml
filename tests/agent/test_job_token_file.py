import os
import stat
import threading
from pathlib import Path
from unittest import mock

from clearml_agent.backend_api.session.job_token_file import read_job_token_file
from clearml_agent.backend_api.session.session import Session
from clearml_agent.helper.job_token import TokenRenewer, write_job_token


def test_the_token_file_is_replaced_and_readable_by_its_owner_only(tmp_path):
    path = tmp_path / "job_token"
    write_job_token(path, "first")
    write_job_token(path, "second")
    assert path.read_text() == "second"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".job_token.")], "no temporary files are left"


def test_a_missing_or_empty_token_file_reads_as_none(tmp_path):
    assert read_job_token_file(None) is None
    assert read_job_token_file(tmp_path / "gone") is None
    (tmp_path / "empty").write_text("\n")
    assert read_job_token_file(tmp_path / "empty") is None


def test_the_token_file_is_read_without_its_newline(tmp_path):
    (tmp_path / "t").write_text("abc.def\n")
    assert read_job_token_file(tmp_path / "t") == "abc.def"


def test_the_renewer_writes_each_renewed_token_and_stops_when_the_server_refuses(tmp_path):
    path = tmp_path / "job_token"
    write_job_token(path, "first")
    renewals = iter([("second", 1.5), None])
    renewer = TokenRenewer(path, lambda: next(renewals), expires_in=1.5)
    renewer.start()
    renewer.join(timeout=20)
    assert not renewer.is_alive(), "a refused renewal ends the renewer"
    assert path.read_text() == "second"


def test_the_renewer_tries_again_after_an_error(tmp_path):
    path = tmp_path / "job_token"
    write_job_token(path, "first")
    calls = []

    def fetch():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("network down")
        return ("second", 1.5) if len(calls) == 2 else None

    renewer = TokenRenewer(path, fetch, expires_in=1.5, retry_sec=0.1)
    renewer.start()
    renewer.join(timeout=20)
    assert len(calls) == 3
    assert path.read_text() == "second"


def test_stop_ends_the_renewer_without_asking_the_server(tmp_path):
    path = tmp_path / "job_token"
    write_job_token(path, "first")
    fetch = mock.Mock()
    renewer = TokenRenewer(path, fetch, expires_in=3600)
    renewer.start()
    renewer.stop()
    renewer.join(timeout=20)
    assert not renewer.is_alive()
    fetch.assert_not_called()
    assert path.read_text() == "first"


def test_a_job_session_refresh_takes_the_token_from_the_file(tmp_path):
    """The job's session keeps the token the agent renews: a refresh reads the file, not the token it started with."""
    path = tmp_path / "job_token"
    write_job_token(path, "started-with")
    session = Session.__new__(Session)
    session._Session__job_token_file = str(path)
    session._Session__job_token = None
    session._Session__device_id = None
    assert session._do_refresh_token("started-with") == "started-with"
    write_job_token(path, "renewed")
    assert session._do_refresh_token("started-with") == "renewed"

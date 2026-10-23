from pathlib import Path

from clearml.backend_api.session.job_token_file import read_job_token_file
from clearml.backend_api.session.session import Session


def job_session(token_file, job_token=None):
    """A session as a job has it: no device, the token from the file, nothing to exchange."""
    session = Session.__new__(Session)
    session._Session__job_token_file = str(token_file) if token_file else None
    session._Session__job_token = job_token
    session._Session__device_id = None
    session._Session__auth_token = None
    return session


def test_a_refresh_in_a_job_takes_the_renewed_token_from_the_file(tmp_path):
    path = tmp_path / "job_token"
    path.write_text("started-with\n")
    session = job_session(path)
    assert session._do_refresh_token("started-with") == "started-with"
    path.write_text("renewed\n")
    assert session._do_refresh_token("started-with") == "renewed"


def test_without_a_token_file_the_job_token_is_used_as_before(tmp_path):
    assert job_session(None, job_token="job-1")._do_refresh_token("job-1") == "job-1"
    assert job_session(tmp_path / "gone", job_token="job-1")._do_refresh_token("job-1") == "job-1"


def test_a_missing_or_empty_file_reads_as_none(tmp_path):
    assert read_job_token_file(None) is None
    assert read_job_token_file(tmp_path / "gone") is None
    (tmp_path / "empty").write_text("\n")
    assert read_job_token_file(tmp_path / "empty") is None

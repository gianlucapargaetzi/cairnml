"""The storage keys are asked for one dataset and one level, and the answer names the prefix the remote is built from."""

from types import SimpleNamespace

import pytest

from cairnml.datasets import DatasetError
from cairnml.datasets.credentials import fetch_storage_keys

ANSWER = {
    "endpoint": "http://store:9000",
    "region": "us-east-1",
    "bucket": "dataset",
    "prefix": "dataset/c1/acme/cars",
    "access_key": "AK",
    "secret_key": "SK",
    "session_token": "ST",
    "expires_at": "2026-11-05T12:00:00+00:00",
}


class FakeSession:
    def __init__(self, data=None, ok=True):
        self.calls = []
        self.data = data
        self.ok = ok

    def send_request(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(ok=self.ok, text="refused", json=lambda: {"data": self.data})


def test_the_request_names_the_repository_and_the_level_at_version_2_40():
    session = FakeSession(ANSWER)

    keys = fetch_storage_keys(repository="acme/cars", level="read", session=session)

    assert session.calls == [{
        "service": "datachunks",
        "action": "dataset_credentials",
        "version": "2.40",
        "json": {"repository": "acme/cars", "level": "read"},
    }]
    assert keys.prefix == "dataset/c1/acme/cars"


def test_an_answer_without_the_prefix_is_refused():
    data = {name: value for name, value in ANSWER.items() if name != "prefix"}

    with pytest.raises(DatasetError, match="prefix"):
        fetch_storage_keys(repository="acme/cars", level="read_write", session=FakeSession(data))

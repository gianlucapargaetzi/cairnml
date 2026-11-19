"""The storage keys are asked for one dataset and one level, and the answer names the prefix the remote is built from."""

import pytest

from cairnml.backend_api.services import datachunks
from cairnml.backend_api.session.callresult import CallResult
from cairnml.backend_api.session.response import ResponseMeta
from cairnml.datasets import DatasetError
from cairnml.datasets.credentials import StorageKeys, fetch_storage_keys

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
        self.sent = []
        self.data = data
        self.ok = ok

    def send(self, req_obj, headers=None):
        self.sent.append(req_obj)
        meta = ResponseMeta.from_raw_data(status_code=200 if self.ok else 400, text="refused", endpoint="x.y")
        return CallResult(meta=meta, response_data=self.data)


def test_the_request_names_the_repository_and_the_level():
    session = FakeSession(ANSWER)

    keys = fetch_storage_keys(repository="acme/cars", level="read", session=session)

    (request,) = session.sent
    assert type(request) is datachunks.DatasetCredentialsRequest
    assert request.to_dict() == {"repository": "acme/cars", "level": "read"}
    assert keys.prefix == "dataset/c1/acme/cars"


def test_an_answer_without_the_prefix_is_refused():
    data = {name: value for name, value in ANSWER.items() if name != "prefix"}

    with pytest.raises(DatasetError, match="prefix"):
        fetch_storage_keys(repository="acme/cars", level="read_write", session=FakeSession(data))


def test_the_secret_and_the_session_token_do_not_appear_in_the_repr():
    keys = StorageKeys(**{**ANSWER, "secret_key": "sekret-value-123", "session_token": "token-value-456"})

    text = repr(keys)

    assert "sekret-value-123" not in text
    assert "token-value-456" not in text
    assert ANSWER["access_key"] in text

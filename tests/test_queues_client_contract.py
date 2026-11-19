"""
What the queues client classes send and parse, pinned before and after the client was generated from the server
schema (cairnml-server#136): the classes keep their names and fields, and only the API version in the URL changes.
"""

import pytest

from cairnml.backend_api.services import API_LEVEL, queues
from cairnml.backend_api.session import Session

# The newest declared version of queues.get_all. The generator labels the definitions by the version in which they
# began: the last change of queues.get_all is 2.21. The request is not sent at that label but at the negotiated API
# level (API_LEVEL here), see tests/test_session_api_version.py.
GET_ALL_VERSION = "2.21"


class _Response:
    status_code = 200
    text = ""

    def __init__(self, data):
        self._data = data

    def json(self):
        meta = {"id": "i", "trx": "t", "result_code": 200, "result_subcode": 0, "result_msg": "OK", "error_stack": ""}
        return {"meta": meta, "data": self._data}


class _RecordingSession(Session):
    """A session that records what it would send, without a server."""

    def __init__(self, data):
        self.calls = []
        self._data = data
        self._offline_mode = False
        self._logger = None

    def send_request(self, **kwargs):
        self.calls.append(kwargs)
        return _Response(self._data)


def test_a_request_needs_its_required_fields():
    assert queues.GetByIdRequest(queue="q").to_dict()["queue"] == "q"
    with pytest.raises(TypeError):
        queues.GetByIdRequest()


def test_a_request_sends_only_the_fields_that_are_set():
    body = queues.GetByIdRequest(queue="q").to_dict()
    assert body == {"queue": "q"}
    body = queues.GetAllRequest(only_fields=["id", "name"]).to_dict()
    assert body["only_fields"] == ["id", "name"]
    assert "name" not in body
    assert queues.GetNumEntriesRequest(queue="q").to_dict()["queue"] == "q"


def test_a_response_parses_its_nested_models():
    response = queues.GetAllResponse.from_dict({"queues": [{"id": "1", "name": "n"}]})
    assert response.queues[0].name == "n"


def test_sending_posts_at_the_negotiated_level_and_parses_the_answer():
    session = _RecordingSession({"queues": [{"id": "1", "name": "n"}]})
    result = session.send(queues.GetAllRequest())
    (call,) = session.calls
    assert (call["service"], call["action"]) == ("queues", "get_all")
    assert queues.GetAllRequest._version == GET_ALL_VERSION
    assert call["version"] == API_LEVEL
    assert result.response.queues[0].name == "n"

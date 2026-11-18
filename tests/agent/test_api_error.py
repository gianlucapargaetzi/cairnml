"""The agent's APIError is the SDK's APIError class: the same code, subcode, message and data for a failed call."""

import json

import requests
from cairnml.backend_api.session.client.client import APIError as SdkAPIError

from cairnml_agent.errors import APIError


def _response(status, body):
    res = requests.Response()
    res.status_code = status
    res._content = json.dumps(body).encode()
    res.encoding = "utf-8"
    return res


def test_the_agent_error_is_the_sdk_error():
    assert issubclass(APIError, SdkAPIError)


def test_a_failed_call_gives_the_code_subcode_and_message_of_the_server_meta():
    body = {
        "meta": {
            "id": "1",
            "trx": "1",
            "endpoint": {"name": "tasks.get_by_id", "requested_version": "2.5", "actual_version": "2.5"},
            "result_code": 400,
            "result_subcode": 123,
            "result_msg": "task not found",
        },
        "data": {},
    }
    error = APIError(_response(400, body))

    assert error.code == 400
    assert error.subcode == 123
    assert error.message == "task not found"
    assert error.codes == (400, 123)
    assert "task not found" in str(error)


def test_a_response_without_a_server_meta_keeps_the_status_and_the_text():
    res = requests.Response()
    res.status_code = 502
    res._content = b"bad gateway"

    error = APIError(res, extra_info="Invalid response")

    assert error.code == 502
    assert error.extra_info == "Invalid response"
    assert "bad gateway" in str(error)

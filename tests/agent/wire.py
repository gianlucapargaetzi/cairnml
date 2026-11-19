"""A session whose HTTP layer is a table of answers: the tests say what goes on the wire, not how the code asks.

`WireSession` is the agent's own session class. Only `send_request` (the one method every request of the SDK's session
ends in, whichever way the caller built it) is replaced: it records the call and answers from the table. A request
built by hand (`send_request(service=..., action=..., json=...)`) and one built from a generated class
(`send(GetAllRequest(...))`) both arrive here, so a test of a call site holds before and after it is moved from one
to the other.
"""

import json
import logging
from types import SimpleNamespace

import requests
from pyhocon import ConfigFactory

from cairnml_agent.session import Session


def answer(data=None, status=200, msg="OK", subcode=0):
    """The server's answer: the envelope the API wraps every result in."""
    response = requests.Response()
    response.status_code = status
    response.reason = msg
    response.encoding = "utf-8"
    response.headers["Content-Type"] = "application/json"
    meta = {
        "id": "id-1",
        "trx": "trx-1",
        "endpoint": {"name": "x.y", "requested_version": "2.41", "actual_version": "2.41"},
        "result_code": status,
        "result_subcode": subcode,
        "result_msg": msg,
    }
    response._content = json.dumps({"meta": meta, "data": data if data is not None else {}}).encode()
    return response


def not_json(status):
    """What a proxy in front of the server answers while the server restarts: no JSON envelope."""
    response = requests.Response()
    response.status_code = status
    response.encoding = "utf-8"
    response._content = b"<html>Bad gateway</html>"
    return response


class WireSession(Session):
    """The agent's session with a table for a server. `answers` maps "service.action" to an answer, a list of answers
    (one per call, the last one stays) or a function of the call."""

    def __init__(self, answers=None, config=None):
        # the real constructor signs in; the tests need the request methods only
        self._logger = logging.getLogger("wire-session")
        self.answers = dict(answers or {})
        self.calls = []
        self.config = ConfigFactory.from_dict(config or {})
        # the SDK's batch requests are split at this size (its configured default)
        self._Session__max_req_size = 15728640

    def send_request(self, service, action, version=None, method=None, headers=None, data=None, json=None, params=None):
        call = SimpleNamespace(
            name=f"{service}.{action}",
            service=service,
            action=action,
            version=version,
            method=method,
            headers=headers,
            data=data,
            json=json,
        )
        self.calls.append(call)
        planned = self.answers[call.name]
        if callable(planned):
            planned = planned(call)
        if isinstance(planned, list):
            planned = planned.pop(0) if len(planned) > 1 else planned[0]
        if isinstance(planned, Exception):
            raise planned
        return planned

    def of(self, name):
        """The calls made to "service.action"."""
        return [call for call in self.calls if call.name == name]

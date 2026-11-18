import inspect
import os
from unittest import mock

import pytest

from cairnml.backend_api.session.client.client import APIClient
from cairnml.backend_api.session.session import Session

HOST = "https://api.example"
WORKER = "worker-1"
CLIENT = "cairnml-test-client"


class Reply:
    status_code = 200
    reason = "OK"


def wire_session():
    session = Session.__new__(Session)
    session._Session__host = HOST
    session._Session__worker = WORKER
    session.client = CLIENT
    session._session_requests = 1
    session._verbose = False
    session._logger = None
    session._Session__http_session = mock.Mock()
    session._Session__http_session_pid = os.getpid()
    session._Session__http_session.request.return_value = Reply()
    return session


def sent_headers(session, **kwargs):
    with mock.patch.object(Session, "token", new_callable=mock.PropertyMock, return_value="tok"):
        session.send_request(service="tasks", action="get_all", json={}, **kwargs)
    return session._Session__http_session.request.call_args.kwargs["headers"]


def test_every_request_carries_the_worker_and_client_headers_with_the_cairnml_names():
    headers = sent_headers(wire_session())

    assert headers["X-CairnML-Worker"] == WORKER
    assert headers["X-CairnML-Client"] == CLIENT
    assert headers["Authorization"] == "Bearer tok"


def test_no_request_carries_the_old_trains_header_names():
    headers = sent_headers(wire_session())

    assert [name for name in headers if name.lower().startswith("x-trains")] == []


def test_send_request_takes_no_async_flag_because_the_server_answers_synchronously():
    assert "async_enable" not in inspect.signature(Session.send_request).parameters
    assert "async_enable" not in inspect.signature(Session.send).parameters
    assert not hasattr(Session, "send_async")


def test_the_api_client_takes_no_api_version_argument():
    with pytest.raises(TypeError):
        APIClient(session=mock.Mock(), api_version="2.41")


def test_the_api_client_exposes_every_service_without_an_api_version():
    fake_session = mock.Mock()
    client = APIClient(session=fake_session)

    assert client.tasks is not None
    assert client.queues is not None

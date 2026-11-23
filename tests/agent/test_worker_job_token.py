from types import SimpleNamespace

from cairnml.backend_api.services import API_LEVEL
from pyhocon import ConfigFactory

from cairnml_agent.commands.worker import Worker

from .wire import WireSession, answer


class FakeSession(WireSession):
    """The agent's session with the server's answers to the job token requests."""

    def __init__(self, *answers):
        super().__init__({"devices.job_token": list(answers)})


def agent_worker(session):
    return SimpleNamespace(_session=session)


def test_the_agent_asks_for_the_job_token_of_the_task_with_its_own_session():
    session = FakeSession(answer({"access_token": "job-1", "expires_in": 3600}))
    assert Worker._get_job_token(agent_worker(session), "task-1") == ("job-1", 3600)
    [call] = session.of("devices.job_token")
    assert (call.service, call.action, call.version) == ("devices", "job_token", API_LEVEL)
    assert call.json == {"task": "task-1"}


def test_a_refused_job_token_is_none_so_the_job_does_not_start():
    session = FakeSession(answer(status=401, msg="task is stopped"))
    assert Worker._get_job_token(agent_worker(session), "task-1") == (None, None)


def test_a_failed_request_is_none_so_the_job_does_not_start():
    session = FakeSession(ConnectionError("server down"))
    assert Worker._get_job_token(agent_worker(session), "task-1") == (None, None)


def test_the_job_configuration_names_no_device(tmp_path):
    config = ConfigFactory.parse_string('api { api_server: "https://api.example", idp { device_id: "dev_agent" } }\n')
    target = tmp_path / "job.conf"
    Worker.dump_config(agent_worker(None), str(target), config=config)
    text = target.read_text()
    assert "api.example" in text
    assert "dev_agent" not in text
    assert "idp" not in text

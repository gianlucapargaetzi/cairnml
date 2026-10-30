from types import SimpleNamespace

from cairnml_agent.commands.worker import Worker
from cairnml_agent.external.pyhocon import ConfigFactory


class Reply:
    def __init__(self, ok, body=None, status_code=200):
        self.ok = ok
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def send_request(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.reply


def agent_worker(session):
    return SimpleNamespace(_session=session)


def test_the_agent_asks_for_the_job_token_of_the_task_with_its_own_session():
    session = FakeSession(Reply(True, {"data": {"access_token": "job-1", "expires_in": 3600}}))
    assert Worker._get_job_token(agent_worker(session), "task-1") == ("job-1", 3600)
    call = session.calls[0]
    assert (call["service"], call["action"], call["version"]) == ("devices", "job_token", "2.8")
    assert call["json"] == {"task": "task-1"}


def test_a_refused_job_token_is_none_so_the_job_does_not_start():
    session = FakeSession(Reply(False, {"meta": {"result_msg": "task is stopped"}}, status_code=401))
    assert Worker._get_job_token(agent_worker(session), "task-1") == (None, None)


def test_a_failed_request_is_none_so_the_job_does_not_start():
    session = FakeSession(error=ConnectionError("server down"))
    assert Worker._get_job_token(agent_worker(session), "task-1") == (None, None)


def test_the_job_configuration_names_no_device(tmp_path):
    config = ConfigFactory.parse_string(
        'api { api_server: "https://api.example", idp { device_id: "dev_agent" } }\n'
    )
    target = tmp_path / "job.conf"
    Worker.dump_config(agent_worker(None), str(target), config=config)
    text = target.read_text()
    assert "api.example" in text
    assert "dev_agent" not in text
    assert "idp" not in text

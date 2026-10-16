from pathlib import Path
from unittest import mock

from clearml_agent.commands import config as init
from clearml_agent.idp.enrol import EnrolmentFailed


def answers(*values):
    remaining = list(values)

    def fake_input(*args):
        return remaining.pop(0) if remaining else ""
    return fake_input


# host, then Web, API, Files (blank accepts), output URI, git user, extra index
SCRIPT = ("https://app.example", "", "", "", "", "", "")


def run_init(tmp_path, enrol):
    conf = tmp_path / "clearml-agent.conf"
    with mock.patch.object(init, "LOCAL_CONFIG_FILES", [str(conf)]), \
            mock.patch.object(init, "input", answers(*SCRIPT), create=True), \
            mock.patch.object(init, "enrol_device", side_effect=enrol) as enrol_mock:
        init.main()
    return conf, enrol_mock


def test_the_agent_is_enrolled_as_an_agent_and_the_file_holds_no_credentials(tmp_path):
    conf, enrol_mock = run_init(tmp_path, enrol=lambda api_host, kind: "dev_agent")

    assert enrol_mock.call_args.kwargs["kind"] == "agent"
    text = conf.read_text()
    assert 'device_id: "dev_agent"' in text
    assert "access_key" not in text
    assert "secret_key" not in text
    assert "credentials {" not in text


def test_a_refused_agent_writes_no_configuration(tmp_path):
    def refused(api_host, kind):
        raise EnrolmentFailed("only an admin may enrol an agent")

    conf, _ = run_init(tmp_path, enrol=refused)
    assert not conf.exists()

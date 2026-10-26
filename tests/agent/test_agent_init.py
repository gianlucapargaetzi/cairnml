from pathlib import Path
from unittest import mock

from cairnml_agent.commands import config as init
from cairnml_agent.idp.enrol import EnrolmentFailed


def answers(*values):
    remaining = list(values)

    def fake_input(*args):
        return remaining.pop(0) if remaining else ""
    return fake_input


# host, then Web, API, Files (blank accepts), output URI, git user, extra index
SCRIPT = ("https://app.example", "", "", "", "", "", "")


def run_init(tmp_path, enrol):
    conf = tmp_path / "cairnml-agent.conf"
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


def test_an_sdk_configuration_file_does_not_block_agent_setup(tmp_path):
    sdk_conf = tmp_path / "cairnml.conf"
    sdk_conf.write_text('api { idp { device_id: "dev_sdk" } }\n')
    agent_conf = tmp_path / "cairnml-agent.conf"
    files = [str(tmp_path / "trains.conf"), str(sdk_conf), str(agent_conf)]

    with mock.patch.object(init, "LOCAL_CONFIG_FILES", files), \
            mock.patch.object(init, "input", answers(*SCRIPT), create=True), \
            mock.patch.object(init, "enrol_device", return_value="dev_agent"):
        init.main()

    assert 'device_id: "dev_agent"' in agent_conf.read_text()
    assert sdk_conf.read_text() == 'api { idp { device_id: "dev_sdk" } }\n'


def test_the_agent_does_not_read_the_sdk_configuration_file():
    from cairnml_agent.backend_config.defs import LOCAL_CONFIG_FILES

    assert not any(Path(f).name == "cairnml.conf" for f in LOCAL_CONFIG_FILES)


def test_the_agent_keeps_its_own_device_key():
    from cairnml_agent.idp.device import DEVICE_KEY_PATH

    assert DEVICE_KEY_PATH == Path.home() / ".cairnml" / "agent" / "device.key"

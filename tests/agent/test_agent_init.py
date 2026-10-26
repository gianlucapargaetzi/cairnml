from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from cairnml_agent.commands import config as init
from cairnml_agent.idp.enrol import EnrolmentFailed


def enrolment(server_url, device_id="dev_agent", web_url=None):
    return SimpleNamespace(device_id=device_id, user="admin", server_url=server_url, web_url=web_url)


def run_init(conf, server, *, enrol_side_effect=None, force=False):
    """cairnml-agent init --server <server>, with the server answering as enrol_side_effect says (default: enrolled).
    The command must not ask anything: input() is replaced by a failure."""
    def default_enrol(api_server, **kwargs):
        return enrolment(api_server)

    with mock.patch.object(init, "enrol", side_effect=enrol_side_effect or default_enrol) as enrol_mock, \
            mock.patch("builtins.input", side_effect=AssertionError("init must not ask questions")):
        code = init.main(server=server, force=force, file=str(conf))
    return code, enrol_mock


def test_the_agent_is_enrolled_as_an_agent_and_the_file_holds_no_credentials(tmp_path):
    conf = tmp_path / "cairnml-agent.conf"
    code, enrol_mock = run_init(conf, "http://cairnml:8008")

    assert code == 0
    assert enrol_mock.call_args.kwargs["kind"] == "agent"
    text = conf.read_text()
    assert 'device_id: "dev_agent"' in text
    assert "access_key" not in text
    assert "secret_key" not in text
    assert "credentials {" not in text


@pytest.mark.parametrize("given, api", [
    ("http://localhost", "http://localhost/api"),
    ("http://192.168.1.237", "http://192.168.1.237/api"),
    ("https://cairnml.example/", "https://cairnml.example/api"),
])
def test_the_api_is_under_api_at_the_address_given(tmp_path, given, api):
    code, enrol_mock = run_init(tmp_path / "cairnml-agent.conf", given)

    assert code == 0
    assert enrol_mock.call_args.args[0] == api


def test_the_web_address_is_written_from_the_server(tmp_path):
    conf = tmp_path / "cairnml-agent.conf"

    def server_names_its_web_address(api_server, **kwargs):
        return enrolment(api_server, web_url="https://cairnml.example")

    code, _ = run_init(conf, "https://cairnml.example", enrol_side_effect=server_names_its_web_address)

    assert code == 0
    assert 'web_server: https://cairnml.example' in conf.read_text()


def test_a_refused_agent_writes_no_configuration(tmp_path, capsys):
    conf = tmp_path / "cairnml-agent.conf"

    def refused(api_server, **kwargs):
        raise EnrolmentFailed("only an admin may enrol an agent")

    code, _ = run_init(conf, "http://cairnml:8008", enrol_side_effect=refused)

    assert code == 1
    assert not conf.exists()
    assert "only an admin may enrol an agent" in capsys.readouterr().err


def test_a_second_init_without_force_is_refused_and_keeps_the_file(tmp_path):
    conf = tmp_path / "cairnml-agent.conf"
    conf.write_text('api { idp { device_id: "dev_old" } }\n')

    def already_enrolled(api_server, *, force=False, previous_device_id=None, **kwargs):
        if previous_device_id and not force:
            raise EnrolmentFailed("this machine is already enrolled; use --force to enroll again")
        return enrolment(api_server)

    code, enrol_mock = run_init(conf, "http://cairnml:8008", enrol_side_effect=already_enrolled)

    assert code == 1
    assert enrol_mock.call_args.kwargs["previous_device_id"] == "dev_old"
    assert conf.read_text() == 'api { idp { device_id: "dev_old" } }\n'


def test_force_enrols_again_with_the_previous_device(tmp_path):
    conf = tmp_path / "cairnml-agent.conf"
    conf.write_text('api { idp { device_id: "dev_old" } }\n')

    code, enrol_mock = run_init(conf, "http://cairnml:8008", force=True)

    assert code == 0
    assert enrol_mock.call_args.kwargs["force"] is True
    assert enrol_mock.call_args.kwargs["previous_device_id"] == "dev_old"
    assert 'device_id: "dev_agent"' in conf.read_text()


def test_an_sdk_configuration_file_is_left_alone(tmp_path):
    sdk_conf = tmp_path / "cairnml.conf"
    sdk_conf.write_text('api { idp { device_id: "dev_sdk" } }\n')
    agent_conf = tmp_path / "cairnml-agent.conf"

    code, _ = run_init(agent_conf, "http://cairnml:8008")

    assert code == 0
    assert 'device_id: "dev_agent"' in agent_conf.read_text()
    assert sdk_conf.read_text() == 'api { idp { device_id: "dev_sdk" } }\n'


def test_the_agent_does_not_read_the_sdk_configuration_file():
    from cairnml_agent.backend_config.defs import LOCAL_CONFIG_FILES

    assert not any(Path(f).name == "cairnml.conf" for f in LOCAL_CONFIG_FILES)


def test_the_agent_keeps_its_own_device_key():
    from cairnml_agent.idp.device import DEVICE_KEY_PATH

    assert DEVICE_KEY_PATH == Path.home() / ".cairnml" / "agent" / "device.key"

import re
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cairnml_agent
import pytest
from cairnml.idp.enrol import EnrolmentFailed
from pyhocon import ConfigFactory, ConfigTree

from cairnml_agent.backend_api.config import load
from cairnml_agent.commands import config as init


def enrolment(server_url, device_id="dev_agent", web_url=None, queue=""):
    return SimpleNamespace(device_id=device_id, user="admin", server_url=server_url, web_url=web_url, queue=queue)


def run_init(conf, server, *, enrol_side_effect=None, force=False):
    """cairnml-agent init <server>, with the server answering as enrol_side_effect says (default: enrolled).
    The command must not ask anything: input() is replaced by a failure."""

    def default_enrol(api_server, **kwargs):
        return enrolment(api_server)

    with (
        mock.patch.object(init, "enrol", side_effect=enrol_side_effect or default_enrol) as enrol_mock,
        mock.patch("builtins.input", side_effect=AssertionError("init must not ask questions")),
    ):
        code = init.main(server=server, force=force, file=str(conf))
    return code, enrol_mock


def test_the_agent_is_enrolled_as_an_agent_and_the_file_holds_no_credentials(tmp_path):
    conf = tmp_path / "cairnml-agent.conf"
    code, enrol_mock = run_init(conf, "http://cairnml")

    assert code == 0
    assert enrol_mock.call_args.kwargs["kind"] == "agent"
    text = conf.read_text()
    assert 'device_id: "dev_agent"' in text
    assert "access_key" not in text
    assert "secret_key" not in text
    assert "credentials {" not in text


@pytest.mark.parametrize(
    "given, api",
    [
        ("http://localhost", "http://localhost/cairnml/api"),
        ("http://192.168.1.237", "http://192.168.1.237/cairnml/api"),
        ("https://cairnml.example/", "https://cairnml.example/cairnml/api"),
        ("http://localhost/cairnml", "http://localhost/cairnml/api"),
        ("http://localhost/cairnml/api", "http://localhost/cairnml/api"),
    ],
)
def test_the_api_is_under_cairnml_api_at_the_address_given(tmp_path, given, api):
    code, enrol_mock = run_init(tmp_path / "cairnml-agent.conf", given)

    assert code == 0
    assert enrol_mock.call_args.args[0] == api


def test_the_web_address_is_written_from_the_server(tmp_path):
    conf = tmp_path / "cairnml-agent.conf"

    def server_names_its_web_address(api_server, **kwargs):
        return enrolment(api_server, web_url="https://cairnml.example")

    code, _ = run_init(conf, "https://cairnml.example", enrol_side_effect=server_names_its_web_address)

    assert code == 0
    assert "web_server: https://cairnml.example" in conf.read_text()


def test_a_refused_agent_writes_no_configuration(tmp_path, capsys):
    conf = tmp_path / "cairnml-agent.conf"

    def refused(api_server, **kwargs):
        raise EnrolmentFailed("only an admin may enrol an agent")

    code, _ = run_init(conf, "http://cairnml", enrol_side_effect=refused)

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

    code, enrol_mock = run_init(conf, "http://cairnml", enrol_side_effect=already_enrolled)

    assert code == 1
    assert enrol_mock.call_args.kwargs["previous_device_id"] == "dev_old"
    assert conf.read_text() == 'api { idp { device_id: "dev_old" } }\n'


def test_force_enrols_again_with_the_previous_device(tmp_path):
    conf = tmp_path / "cairnml-agent.conf"
    conf.write_text('api { idp { device_id: "dev_old" } }\n')

    code, enrol_mock = run_init(conf, "http://cairnml", force=True)

    assert code == 0
    assert enrol_mock.call_args.kwargs["force"] is True
    assert enrol_mock.call_args.kwargs["previous_device_id"] == "dev_old"
    assert 'device_id: "dev_agent"' in conf.read_text()


def test_an_sdk_configuration_file_is_left_alone(tmp_path):
    sdk_conf = tmp_path / "cairnml.conf"
    sdk_conf.write_text('api { idp { device_id: "dev_sdk" } }\n')
    agent_conf = tmp_path / "cairnml-agent.conf"

    code, _ = run_init(agent_conf, "http://cairnml")

    assert code == 0
    assert 'device_id: "dev_agent"' in agent_conf.read_text()
    assert sdk_conf.read_text() == 'api { idp { device_id: "dev_sdk" } }\n'


def test_the_agent_does_not_read_the_sdk_configuration_file():
    from cairnml_agent.backend_config.defs import LOCAL_CONFIG_FILES

    assert not any(Path(f).name == "cairnml.conf" for f in LOCAL_CONFIG_FILES)


def test_the_agent_keeps_its_own_device_key():
    from cairnml_agent.idp.device import DEVICE_KEY_PATH

    assert DEVICE_KEY_PATH == Path.home() / ".cairnml" / "agent" / "device.key"


def test_the_enrolment_writes_the_key_to_the_agents_own_path(tmp_path):
    from cairnml_agent.idp.device import DEVICE_KEY_PATH

    _, enrol_mock = run_init(tmp_path / "cairnml-agent.conf", "http://localhost")
    assert enrol_mock.call_args.kwargs["key_path"] == DEVICE_KEY_PATH


def test_the_server_is_the_first_argument_of_the_command(capsys):
    from cairnml_agent.interface import get_parser

    parser = get_parser()
    assert parser.parse_args(["init", "http://localhost", "--force"]).server == "http://localhost"
    with pytest.raises(SystemExit):
        parser.parse_args(["init"])
    assert "server" in capsys.readouterr().err


def test_the_queue_of_the_machine_is_printed_as_the_exact_name_to_use(tmp_path, capsys):
    def server_makes_a_queue(api_server, **kwargs):
        return enrolment(api_server, queue="parg-nb-b59488")

    code, _ = run_init(tmp_path / "cairnml-agent.conf", "http://cairnml", enrol_side_effect=server_makes_a_queue)

    assert code == 0
    out = capsys.readouterr().out
    assert "queue of this machine: parg-nb-b59488" in out
    assert "exact name" in out


def test_no_queue_line_is_printed_for_an_enrolment_without_a_queue(tmp_path, capsys):
    code, _ = run_init(tmp_path / "cairnml-agent.conf", "http://cairnml")

    assert code == 0
    assert "queue of this machine" not in capsys.readouterr().out


def written_file(folder, queue="q-1"):
    conf = folder / "cairnml-agent.conf"
    assert init.write_config(conf, "http://cairnml/cairnml/api", "http://cairnml/cairnml", "dev_x", queue=queue)
    return conf


def leaf_keys(tree, prefix=""):
    keys = set()
    for key, value in tree.items():
        if isinstance(value, ConfigTree):
            keys |= leaf_keys(value, f"{prefix}{key}.")
        else:
            keys.add(f"{prefix}{key}")
    return keys


def packaged_default_keys():
    defaults = Path(cairnml_agent.__file__).parent / "backend_api" / "config" / "default"
    keys = set()
    for name in ("agent", "sdk"):
        keys |= {f"{name}.{key}" for key in leaf_keys(ConfigFactory.parse_file(str(defaults / f"{name}.conf")))}
    return keys


def test_the_file_written_by_init_holds_no_copy_of_the_defaults(tmp_path):
    conf = written_file(tmp_path)

    parsed = ConfigFactory.parse_file(str(conf))
    assert "default_docker" not in parsed.get("agent", {})
    assert not (leaf_keys(parsed) & packaged_default_keys())
    assert len(conf.read_text().splitlines()) < 40


def test_an_upgrade_changes_the_job_image_of_a_written_file(tmp_path, monkeypatch):
    conf = written_file(tmp_path)
    newer = tmp_path / "newer" / "config" / "default"
    newer.mkdir(parents=True)
    (newer / "agent.conf").write_text('default_docker {\n    image: "new/default:2"\n}\n')
    monkeypatch.setenv("CAIRNML_CONFIG_FILE", str(conf))
    monkeypatch.delenv("CAIRNML_CONFIG_PATH", raising=False)

    config = load(str(tmp_path / "newer" / "module.py"))

    assert config.get("agent.default_docker.image") == "new/default:2"


def test_a_hand_written_image_is_honoured_and_an_old_git_user_is_only_kept_in_the_file(tmp_path, monkeypatch):
    conf = written_file(tmp_path)
    conf.write_text(conf.read_text() + 'agent.default_docker.image: "my/own:1"\nagent.git_user="bob"\n')
    monkeypatch.setenv("CAIRNML_CONFIG_FILE", str(conf))
    monkeypatch.delenv("CAIRNML_CONFIG_PATH", raising=False)

    config = load()

    assert config.get("agent.default_docker.image") == "my/own:1"
    # the line stays in the file the user wrote; the agent no longer reads it (it warns about it at start)
    assert config.get("agent.git_user") == "bob"
    assert config.get("agent.queue") == "q-1"
    assert config.get("api.idp.device_id") == "dev_x"


def test_the_header_keeps_the_lines_the_other_tools_read(tmp_path):
    text = written_file(tmp_path / "queued").read_text()
    assert "api_server: http://cairnml/cairnml/api" in text
    assert "web_server: http://cairnml/cairnml" in text
    assert 'device_id: "dev_x"' in text
    assert re.search(r'agent\.queue\s*=\s*"([^"]+)"', text).group(1) == "q-1"

    unqueued = written_file(tmp_path / "unqueued", queue="").read_text()
    assert "agent.queue" not in unqueued


def test_the_package_index_is_a_hint_and_the_file_offers_no_git_credential(tmp_path):
    conf = written_file(tmp_path)
    parsed = ConfigFactory.parse_file(str(conf))
    assert parsed.get("agent.package_manager.extra_index_url", None) is None

    text = conf.read_text()
    assert any(line.startswith("#") and "extra_index_url" in line for line in text.splitlines())
    # the agent has no Git credentials of its own, so the file offers no place to put one
    assert "git_user" not in text and "git_pass" not in text and "ssh" not in text.lower()

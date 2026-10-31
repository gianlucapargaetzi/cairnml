from pathlib import Path
from unittest import mock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cairnml.cli import enrolment
from cairnml.idp.device import save_device_key
from cairnml.idp.enrol import Enrolment, EnrolmentFailed

PUBLIC = "http://cairnml:8008"


def test_init_needs_the_server_on_the_command_line(tmp_path):
    with pytest.raises(SystemExit):
        enrolment.init_main(["--file", str(tmp_path / "cairnml.conf")])


def test_init_writes_the_servers_own_address_and_the_device(tmp_path, capsys):
    conf = tmp_path / "cairnml.conf"
    result = Enrolment(device_id="dev_9", server_url=PUBLIC, web_url="http://cairnml", user="alice")
    with mock.patch.object(enrolment, "enrol", return_value=result) as enrol:
        code = enrolment.init_main(["http://localhost", "--file", str(conf)])
    assert code == 0
    assert enrol.call_args.args[0] == "http://localhost/cairnml/api"
    text = conf.read_text()
    assert f'api_server: {PUBLIC}' in text
    assert 'device_id: "dev_9"' in text
    assert "enrolled as alice" in capsys.readouterr().out


def test_init_refuses_when_enrolled_and_passes_the_previous_device_on_force(tmp_path):
    conf = tmp_path / "cairnml.conf"
    conf.write_text(f'api {{ api_server: "{PUBLIC}"\n idp {{ device_id: "dev_old" }} }}\n')
    result = Enrolment(device_id="dev_new", server_url=PUBLIC, web_url="", user="bob")
    with mock.patch.object(enrolment, "enrol", return_value=result) as enrol:
        enrolment.init_main([PUBLIC, "--force", "--file", str(conf)])
    assert enrol.call_args.kwargs["previous_device_id"] == "dev_old"
    assert enrol.call_args.kwargs["force"] is True


def test_init_reports_a_refusal_and_writes_nothing(tmp_path, capsys):
    conf = tmp_path / "cairnml.conf"
    with mock.patch.object(enrolment, "enrol", side_effect=EnrolmentFailed("only an admin may enrol an agent")):
        assert enrolment.init_main([PUBLIC, "--file", str(conf)]) == 1
    assert not conf.exists()
    assert "only an admin" in capsys.readouterr().err


def test_init_prints_the_upgrade_notice_once_on_stderr(tmp_path, capsys):
    conf = tmp_path / "cairnml.conf"
    notice = "the SDK (cairnml) 0.0.1 is older than this server's minimum 0.0.2: upgrade cairnml."
    result = Enrolment(device_id="dev_9", server_url=PUBLIC, web_url="", user="alice", upgrade=notice)
    with mock.patch.object(enrolment, "enrol", return_value=result):
        assert enrolment.init_main([PUBLIC, "--file", str(conf)]) == 0
    captured = capsys.readouterr()
    assert captured.err.count("upgrade cairnml") == 1
    assert "upgrade" not in captured.out


def test_init_prints_no_notice_when_the_sdk_is_current(tmp_path, capsys):
    conf = tmp_path / "cairnml.conf"
    result = Enrolment(device_id="dev_9", server_url=PUBLIC, web_url="", user="alice")
    with mock.patch.object(enrolment, "enrol", return_value=result):
        enrolment.init_main([PUBLIC, "--file", str(conf)])
    assert capsys.readouterr().err == ""


def test_whoami_says_so_when_not_enrolled(tmp_path, capsys):
    assert enrolment.whoami_main(["--file", str(tmp_path / "cairnml.conf")]) == 1
    assert "not enrolled" in capsys.readouterr().err


def test_logout_keeps_the_key_when_the_device_cannot_be_revoked(tmp_path, capsys):
    conf = tmp_path / "cairnml.conf"
    conf.write_text(f'api {{ api_server: "{PUBLIC}"\n idp {{ device_id: "dev_1" }} }}\n')
    key = tmp_path / "device.key"
    save_device_key(key, Ed25519PrivateKey.generate())
    with mock.patch.object(enrolment, "revoke_device", side_effect=EnrolmentFailed("server unreachable")), \
            mock.patch.object(enrolment, "DEVICE_KEY_PATH", key):
        assert enrolment.logout_main(["--file", str(conf)]) == 1
    assert key.exists()
    assert 'device_id: "dev_1"' in conf.read_text()


def test_logout_revokes_then_removes_the_key_and_the_device(tmp_path):
    conf = tmp_path / "cairnml.conf"
    conf.write_text(f'api {{ api_server: "{PUBLIC}"\n idp {{ device_id: "dev_1" }} }}\n')
    key = tmp_path / "device.key"
    save_device_key(key, Ed25519PrivateKey.generate())
    with mock.patch.object(enrolment, "revoke_device") as revoke, \
            mock.patch.object(enrolment, "DEVICE_KEY_PATH", key):
        assert enrolment.logout_main(["--file", str(conf)]) == 0
    revoke.assert_called_once()
    assert not key.exists()
    assert 'device_id: ""' in conf.read_text()


def test_the_address_given_is_the_server_and_the_api_is_under_cairnml_api(tmp_path):
    conf = tmp_path / "cairnml.conf"
    result = Enrolment(device_id="dev_7", server_url=PUBLIC, web_url="", user="alice")
    with mock.patch.object(enrolment, "enrol", return_value=result) as enrol:
        enrolment.init_main(["http://192.168.1.20", "--file", str(conf)])
    assert enrol.call_args.args[0] == "http://192.168.1.20/cairnml/api"


@pytest.mark.parametrize("given, api", [
    ("http://localhost", "http://localhost/cairnml/api"),
    ("http://localhost/", "http://localhost/cairnml/api"),
    ("http://localhost/cairnml", "http://localhost/cairnml/api"),
    ("https://example.org/cairnml/", "https://example.org/cairnml/api"),
    ("http://localhost/cairnml/api", "http://localhost/cairnml/api"),
])
def test_the_prefix_is_added_once(tmp_path, given, api):
    result = Enrolment(device_id="dev_7", server_url=PUBLIC, web_url="", user="alice")
    with mock.patch.object(enrolment, "enrol", return_value=result) as enrol:
        enrolment.init_main([given, "--file", str(tmp_path / "cairnml.conf")])
    assert enrol.call_args.args[0] == api

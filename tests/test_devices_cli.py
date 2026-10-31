from unittest import mock

import pytest
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cairnml.cli import __main__ as cli
from cairnml.cli import devices
from cairnml.idp.device import save_device_key
from cairnml.idp.enrol import EnrolmentFailed
from cairnml.cli.enrolment import ClientSession

PUBLIC = "http://cairnml:8008"
DEVICES = [
    {"id": "dev_1", "user": "usr_alice", "kind": "client", "name": "laptop", "created": "2026-10-01T10:00:00",
     "last_used": "2026-10-30T08:15:00", "revoked": False},
    {"id": "dev_2", "user": "usr_bob", "kind": "agent", "name": "gpu-box", "created": "2026-10-02T10:00:00",
     "last_used": None, "revoked": True},
]


@pytest.fixture
def enrolled(tmp_path):
    conf = tmp_path / "cairnml.conf"
    conf.write_text(f'api {{ api_server: "{PUBLIC}"\n idp {{ device_id: "dev_1" }} }}\n')
    key = tmp_path / "device.key"
    save_device_key(key, Ed25519PrivateKey.generate())
    return conf, key


def test_list_prints_each_device_with_its_owner_last_seen_and_state(enrolled, capsys):
    conf, key = enrolled
    with mock.patch.object(devices, "list_devices", return_value=DEVICES) as list_devices, \
            mock.patch.object(devices, "DEVICE_KEY_PATH", key):
        assert cli.main(["devices", "list", "--file", str(conf)]) == 0
    list_devices.assert_called_once_with(PUBLIC, "dev_1", key_path=key)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["ID", "NAME", "OWNER", "LAST", "SEEN", "STATE"]
    assert "dev_1" in lines[1] and "laptop" in lines[1] and "usr_alice" in lines[1]
    assert "2026-10-30T08:15:00" in lines[1] and lines[1].endswith("active")
    assert "never" in lines[2] and lines[2].endswith("revoked")


def test_list_says_so_when_there_are_no_devices(enrolled, capsys):
    conf, key = enrolled
    with mock.patch.object(devices, "list_devices", return_value=[]), \
            mock.patch.object(devices, "DEVICE_KEY_PATH", key):
        assert devices.list_main(["--file", str(conf)]) == 0
    assert capsys.readouterr().out == "no devices\n"


def test_list_needs_an_enrolment(tmp_path, capsys):
    assert devices.list_main(["--file", str(tmp_path / "cairnml.conf")]) == 1
    assert "not enrolled" in capsys.readouterr().err


def test_list_reports_a_server_error(enrolled, capsys):
    conf, key = enrolled
    with mock.patch.object(devices, "list_devices", side_effect=EnrolmentFailed("no access")), \
            mock.patch.object(devices, "DEVICE_KEY_PATH", key):
        assert devices.list_main(["--file", str(conf)]) == 1
    assert "no access" in capsys.readouterr().err


def test_revoke_asks_first_and_revokes_nothing_when_refused(enrolled, monkeypatch, capsys):
    conf, key = enrolled
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    with mock.patch.object(devices, "revoke_other_device") as revoke, \
            mock.patch.object(devices, "DEVICE_KEY_PATH", key):
        assert cli.main(["devices", "revoke", "dev_2", "--file", str(conf)]) == 1
    revoke.assert_not_called()
    assert "not revoked" in capsys.readouterr().out


def test_revoke_without_a_terminal_answer_revokes_nothing(enrolled, monkeypatch):
    conf, key = enrolled

    def no_input(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_input)
    with mock.patch.object(devices, "revoke_other_device") as revoke, \
            mock.patch.object(devices, "DEVICE_KEY_PATH", key):
        assert devices.revoke_main(["dev_2", "--file", str(conf)]) == 1
    revoke.assert_not_called()


def test_revoke_after_a_yes_answer(enrolled, monkeypatch, capsys):
    conf, key = enrolled
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    with mock.patch.object(devices, "revoke_other_device", return_value=DEVICES[1]) as revoke, \
            mock.patch.object(devices, "DEVICE_KEY_PATH", key):
        assert devices.revoke_main(["dev_2", "--file", str(conf)]) == 0
    revoke.assert_called_once_with(PUBLIC, "dev_1", "dev_2", key_path=key)
    assert "revoked device dev_2 (gpu-box)" in capsys.readouterr().out


def test_yes_skips_the_question(enrolled):
    conf, key = enrolled
    with mock.patch("builtins.input") as ask, \
            mock.patch.object(devices, "revoke_other_device", return_value=DEVICES[0]) as revoke, \
            mock.patch.object(devices, "DEVICE_KEY_PATH", key):
        assert cli.main(["devices", "revoke", "dev_1", "--yes", "--file", str(conf)]) == 0
    ask.assert_not_called()
    revoke.assert_called_once()


def test_a_failed_revoke_is_reported_and_exits_1(enrolled, capsys):
    conf, key = enrolled
    with mock.patch.object(devices, "revoke_other_device", side_effect=requests.ConnectionError("down")), \
            mock.patch.object(devices, "DEVICE_KEY_PATH", key):
        assert devices.revoke_main(["dev_2", "--yes", "--file", str(conf)]) == 1
    assert "the device was not revoked" in capsys.readouterr().err


def test_an_unknown_subcommand_is_refused(capsys):
    assert devices.devices_main(["delete"]) == 2
    assert "unknown subcommand 'delete'" in capsys.readouterr().err


def test_devices_without_subcommand_prints_usage(capsys):
    assert devices.devices_main([]) == 2
    assert "usage: cairnml devices" in capsys.readouterr().out


class TokenServer:
    """Answers the sign-in with the device and the devices list, like the API does."""

    def __init__(self):
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        if url.endswith("devices.exchange"):
            return FakeResponse({"access_token": "device-token"})
        return FakeResponse({"devices": DEVICES})


class FakeResponse:
    status_code = 200
    headers = {}

    def __init__(self, data):
        self._body = {"data": data}

    def json(self):
        return self._body


def test_list_devices_calls_get_all_with_the_devices_own_token(tmp_path):
    server = TokenServer()
    key = tmp_path / "device.key"
    save_device_key(key, Ed25519PrivateKey.generate())
    assert devices.list_devices(PUBLIC, "dev_1", key_path=key, http=server) == DEVICES
    url, body, headers = server.calls[-1]
    assert url == PUBLIC + "/v2.8/devices.get_all"
    assert headers["Authorization"] == "Bearer device-token"


def test_every_call_names_this_sdk_and_keeps_the_upgrade_notice(tmp_path):
    notice = "the SDK (cairnml) 0.0.1 is older than this server's minimum 0.0.2: upgrade cairnml."
    inner = mock.Mock()
    inner.post.return_value = mock.Mock(headers={"X-CairnML-Upgrade": notice})
    client = ClientSession(session=inner)
    client.post("http://cairnml/v2.8/devices.idp_settings", json={}, headers=None, timeout=30)
    client.post("http://cairnml/v2.8/devices.get_all", json={}, headers={"Authorization": "Bearer t"}, timeout=30)
    first_headers = inner.post.call_args_list[0].kwargs["headers"]
    assert first_headers == {"X-CairnML-Client": "cairnml-0.0.2"}
    assert inner.post.call_args_list[1].kwargs["headers"] == {"X-CairnML-Client": "cairnml-0.0.2",
                                                             "Authorization": "Bearer t"}
    assert client.upgrade == notice

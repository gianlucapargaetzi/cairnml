import pytest
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cairnml.cli import __main__ as cli
from cairnml.cli import doctor
from cairnml.idp.device import save_device_key

SERVER = "http://cairnml"
PUBLIC = "http://cairnml:8008"


class FakeResponse:
    def __init__(self, status_code, data=None, message=""):
        self.status_code = status_code
        self._body = {"data": data} if status_code == 200 else {"meta": {"result_msg": message}}

    def json(self):
        return self._body


class FakeServer:
    """Answers the devices and users calls the doctor makes, like the CairnML API does."""

    def __init__(self, reachable=True, enrolled=True, user=None):
        self.reachable = reachable
        self.enrolled = enrolled
        self.user = user or {"name": "alice", "role": "admin"}
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        if not self.reachable:
            raise requests.ConnectionError("refused")
        self.calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("devices.idp_settings"):
            return FakeResponse(200, {"public_url": PUBLIC, "issuer": "x", "sdk_client_id": "cairnml-sdk"})
        if url.endswith("devices.exchange"):
            if not self.enrolled:
                return FakeResponse(401, message="device is revoked")
            return FakeResponse(200, {"access_token": "token"})
        if url.endswith("users.get_current_user"):
            return FakeResponse(200, {"user": self.user})
        return FakeResponse(404, message="unknown endpoint")


def write_conf(path, server=PUBLIC, device="dev_1"):
    path.write_text(f'api {{ api_server: "{server}"\n idp {{ device_id: "{device}" }} }}\n')
    return path


def make_key(path):
    save_device_key(path, Ed25519PrivateKey.generate())
    path.chmod(0o600)
    return path


@pytest.fixture(autouse=True)
def storage_open(monkeypatch):
    """The object storage answers on port 9000 unless a test says otherwise."""
    opened = []

    def open_port(host, port):
        opened.append((host, port))
        return _Connection()

    monkeypatch.setattr(doctor, "_open_port", open_port)
    return opened


class _Connection:
    def close(self):
        pass


def by_name(checks):
    return {c.name: c for c in checks}


def test_a_healthy_machine_passes_every_check(tmp_path):
    conf = write_conf(tmp_path / "cairnml.conf")
    key = make_key(tmp_path / "device.key")
    checks = doctor.run_checks(conf, key_path=key, http=FakeServer())
    assert [c.name for c in checks] == ["config", "server", "sign-in", "device key", "storage"]
    assert all(c.ok is True for c in checks), [c.line() for c in checks]
    assert "alice (admin)" in by_name(checks)["sign-in"].message


def test_a_missing_configuration_file_is_the_first_failure(tmp_path):
    key = make_key(tmp_path / "device.key")
    checks = by_name(doctor.run_checks(tmp_path / "missing.conf", key_path=key, http=FakeServer()))
    assert checks["config"].ok is False
    assert "cairnml init <server address>" in checks["config"].message
    assert checks["server"].ok is None  # nothing to check without a server
    assert checks["sign-in"].ok is None


def test_an_unreachable_server_fails_the_server_check_and_skips_sign_in(tmp_path):
    conf = write_conf(tmp_path / "cairnml.conf")
    key = make_key(tmp_path / "device.key")
    checks = by_name(doctor.run_checks(conf, key_path=key, http=FakeServer(reachable=False)))
    assert checks["config"].ok is True
    assert checks["server"].ok is False
    assert "cannot reach" in checks["server"].message
    assert "check the address" in checks["server"].message
    assert checks["sign-in"].ok is False  # the sign-in reports the same unreachable server
    assert checks["device key"].ok is True


def test_a_refused_device_fails_the_sign_in_with_the_next_step(tmp_path):
    conf = write_conf(tmp_path / "cairnml.conf")
    key = make_key(tmp_path / "device.key")
    checks = by_name(doctor.run_checks(conf, key_path=key, http=FakeServer(enrolled=False)))
    assert checks["server"].ok is True
    assert checks["sign-in"].ok is False
    assert "device is revoked" in checks["sign-in"].message
    assert "--force" in checks["sign-in"].message


def test_a_missing_device_key_fails_the_key_and_skips_sign_in(tmp_path):
    conf = write_conf(tmp_path / "cairnml.conf")
    checks = by_name(doctor.run_checks(conf, key_path=tmp_path / "none.key", http=FakeServer()))
    assert checks["device key"].ok is False
    assert "no device key" in checks["device key"].message
    assert checks["sign-in"].ok is None


def test_a_key_readable_by_others_is_refused_with_the_chmod_step(tmp_path):
    conf = write_conf(tmp_path / "cairnml.conf")
    key = make_key(tmp_path / "device.key")
    key.chmod(0o644)
    checks = by_name(doctor.run_checks(conf, key_path=key, http=FakeServer()))
    assert checks["device key"].ok is False
    assert f"chmod 600 {key}" in checks["device key"].message
    assert checks["sign-in"].ok is None


def test_a_signed_out_machine_says_so_in_the_sign_in_check(tmp_path):
    conf = write_conf(tmp_path / "cairnml.conf", device="")
    key = make_key(tmp_path / "device.key")
    checks = by_name(doctor.run_checks(conf, key_path=key, http=FakeServer()))
    assert checks["sign-in"].ok is False
    assert "not enrolled" in checks["sign-in"].message


def test_the_command_prints_one_line_per_check_and_exits_zero_when_all_pass(tmp_path, capsys):
    conf = write_conf(tmp_path / "cairnml.conf")
    key = make_key(tmp_path / "device.key")
    with _patched(doctor, key, FakeServer()):
        assert doctor.doctor_main(["--file", str(conf)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 5
    assert lines[0].startswith("ok")
    assert all(line.startswith("ok") for line in lines)


def test_the_storage_check_fails_with_the_port_and_the_next_step(tmp_path, monkeypatch):
    conf = write_conf(tmp_path / "cairnml.conf", server="http://cairnml:8008")
    key = make_key(tmp_path / "device.key")

    def blocked(host, port):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(doctor, "_open_port", blocked)
    checks = by_name(doctor.run_checks(conf, key_path=key, http=FakeServer()))
    assert checks["server"].ok is True  # the API answers; only the storage port is blocked
    assert checks["storage"].ok is False
    assert "cairnml:9000" in checks["storage"].message
    assert "port 9000" in checks["storage"].message
    assert "firewall" in checks["storage"].message


def test_the_storage_check_uses_the_host_of_the_server_and_port_9000(tmp_path, storage_open):
    conf = write_conf(tmp_path / "cairnml.conf", server="http://cairnml:8008")
    key = make_key(tmp_path / "device.key")
    doctor.run_checks(conf, key_path=key, http=FakeServer())
    assert storage_open == [("cairnml", 9000)]


def test_the_command_exits_one_and_names_the_failures(tmp_path, capsys):
    conf = write_conf(tmp_path / "cairnml.conf")
    with _patched(doctor, tmp_path / "none.key", FakeServer(reachable=False)):
        assert doctor.doctor_main(["--file", str(conf)]) == 1
    out = capsys.readouterr()
    assert "FAIL server" in out.out
    assert "skip" in out.out
    assert "did not pass" in out.err


def test_doctor_is_a_cairnml_command(monkeypatch):
    monkeypatch.setattr(doctor, "doctor_main", lambda: 0)
    import sys

    monkeypatch.setattr(sys, "argv", ["cairnml"])
    assert "doctor" in cli.COMMANDS
    assert cli.main(["doctor"]) == 0


class _patched:
    """Point the doctor at a fake server and a key of the test's own."""

    def __init__(self, module, key, server):
        self.module, self.key, self.server = module, key, server

    def __enter__(self):
        self._orig = self.module.run_checks

        def run(conf, key_path=None, http=None):
            return self._orig(conf, key_path=self.key, http=self.server)

        self.module.run_checks = run
        return self

    def __exit__(self, *exc):
        self.module.run_checks = self._orig

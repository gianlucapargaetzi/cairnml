from pathlib import Path

import cairnml
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cairnml.cli import __main__ as cli
from cairnml.cli import doctor
from cairnml.idp.device import save_device_key

SERVER = "http://cairnml"
PUBLIC = "http://cairnml:8008"


UPGRADE = "the SDK (cairnml) 0.0.1 is older than this server's minimum 0.0.2: upgrade cairnml"


class FakeResponse:
    def __init__(self, status_code, data=None, message="", headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = {"data": data} if status_code == 200 else {"meta": {"result_msg": message}}

    def json(self):
        return self._body


class FakeServer:
    """Answers the devices and users calls the doctor makes, like the CairnML API does."""

    def __init__(self, reachable=True, enrolled=True, user=None, queues=None, workers=None, old_client=False):
        self.reachable = reachable
        self.enrolled = enrolled
        self.user = user or {"name": "alice", "role": "admin"}
        self.queues = [{"id": "q1", "name": "default"}] if queues is None else queues
        self.workers = [{"id": "w1", "queues": [{"name": "default"}]}] if workers is None else workers
        self.old_client = old_client
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        if not self.reachable:
            raise requests.ConnectionError("refused")
        self.calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("devices.idp_settings"):
            headers = {"X-CairnML-Upgrade": UPGRADE} if self.old_client else None
            return FakeResponse(200, {"public_url": PUBLIC, "issuer": "x", "sdk_client_id": "cairnml-sdk"},
                                headers=headers)
        if url.endswith("devices.exchange"):
            if not self.enrolled:
                return FakeResponse(401, message="device is revoked")
            return FakeResponse(200, {"access_token": "token"})
        if url.endswith("users.get_current_user"):
            return FakeResponse(200, {"user": self.user})
        if url.endswith("queues.get_all"):
            return FakeResponse(200, {"queues": self.queues})
        if url.endswith("workers.get_all"):
            return FakeResponse(200, {"workers": self.workers})
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
    assert [c.name for c in checks] == [
        "config", "server", "version", "sign-in", "device key", "queues", "storage", "agent package",
    ]
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
    assert len(lines) == 8
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


def test_an_sdk_below_the_server_minimum_gets_a_warning_with_the_upgrade_step(tmp_path):
    conf = write_conf(tmp_path / "cairnml.conf")
    key = make_key(tmp_path / "device.key")
    checks = by_name(doctor.run_checks(conf, key_path=key, http=FakeServer(old_client=True)))
    assert checks["version"].ok is True and checks["version"].warning is True
    assert "minimum 0.0.2" in checks["version"].message
    assert "pip install -U cairnml" in checks["version"].message
    assert checks["version"].line().startswith("warn")


def test_a_current_sdk_passes_the_version_check(tmp_path):
    conf = write_conf(tmp_path / "cairnml.conf")
    key = make_key(tmp_path / "device.key")
    checks = by_name(doctor.run_checks(conf, key_path=key, http=FakeServer()))
    assert checks["version"].ok is True and checks["version"].warning is False


def test_a_queue_without_a_listening_worker_is_a_warning_with_the_remedy(tmp_path):
    conf = write_conf(tmp_path / "cairnml.conf")
    key = make_key(tmp_path / "device.key")
    server = FakeServer(queues=[{"id": "q1", "name": "default"}, {"id": "q2", "name": "gpu"}],
                        workers=[{"id": "w1", "queues": [{"name": "default"}]}])
    checks = by_name(doctor.run_checks(conf, key_path=key, http=server))
    assert checks["queues"].ok is True and checks["queues"].warning is True
    assert "no worker listens to gpu" in checks["queues"].message
    assert "cairnml-agent daemon --queue" in checks["queues"].message
    assert checks["queues"].line().startswith("warn")


def test_queues_with_workers_pass_and_no_worker_listening_warns_for_each(tmp_path):
    conf = write_conf(tmp_path / "cairnml.conf")
    key = make_key(tmp_path / "device.key")
    served = by_name(doctor.run_checks(conf, key_path=key, http=FakeServer()))["queues"]
    assert served.ok is True and served.warning is False
    unserved = by_name(doctor.run_checks(conf, key_path=key, http=FakeServer(workers=[])))["queues"]
    assert unserved.warning is True and "no worker listens to default" in unserved.message


def test_a_warning_does_not_fail_the_command(tmp_path, capsys):
    conf = write_conf(tmp_path / "cairnml.conf")
    key = make_key(tmp_path / "device.key")
    with _patched(doctor, key, FakeServer(workers=[])):
        assert doctor.doctor_main(["--file", str(conf)]) == 0
    assert "warn" in capsys.readouterr().out


DEFAULTS = Path(cairnml.__file__).parent / "config" / "default"
PACKAGED_SDK_KEYS = 45


def copy_of_defaults(path, default_base_dir=None):
    """A file as an older cairnml init wrote it: the api block, then the packaged sdk.conf (the copy's 'sdk {' line)."""
    sdk = (DEFAULTS / "sdk.conf").read_text()
    if default_base_dir is not None:
        sdk = sdk.replace('default_base_dir: "~/.cairnml/cache"', f'default_base_dir: "{default_base_dir}"', 1)
    path.write_text(
        f'# CairnML SDK configuration file\napi {{ api_server: "{SERVER}"\n idp {{ device_id: "dev_1" }} }}\n'
        "sdk " + sdk
    )
    return path


def test_a_file_that_repeats_the_defaults_is_a_warning_with_the_step(tmp_path, capsys):
    conf = copy_of_defaults(tmp_path / "old.conf")

    config = by_name(doctor.run_checks(conf, key_path=make_key(tmp_path / "device.key"), http=FakeServer()))["config"]

    assert config.ok is True and config.warning is True
    assert config.line().startswith("warn config")
    assert f"repeats {PACKAGED_SDK_KEYS} default settings" in config.message
    assert "do not follow upgrades" in config.message
    assert "delete everything from the line 'sdk {' to the end of the file" in config.message
    with _patched(doctor, make_key(tmp_path / "other.key"), FakeServer()):
        assert doctor.doctor_main(["--file", str(conf)]) == 0


def test_a_copy_that_pins_a_value_names_the_key_and_this_versions_default(tmp_path):
    conf = copy_of_defaults(tmp_path / "old.conf", default_base_dir="/old/cache")

    config = by_name(doctor.run_checks(conf, key_path=make_key(tmp_path / "device.key"), http=FakeServer()))["config"]

    assert config.warning is True
    assert "sdk.storage.cache.default_base_dir" in config.message
    assert '"/old/cache"' in config.message
    assert '"~/.cairnml/cache"' in config.message


def test_a_file_from_init_or_with_a_few_hand_set_keys_is_not_a_warning(tmp_path):
    from cairnml.cli.config.__main__ import write_config

    written = tmp_path / "written.conf"
    write_config(written, SERVER, PUBLIC, "dev_1")
    hand = write_conf(tmp_path / "hand.conf")
    hand.write_text(hand.read_text() + 'sdk { storage { cache { default_base_dir: "/mine" } } }\n')
    key = make_key(tmp_path / "device.key")

    for conf in (written, hand):
        config = by_name(doctor.run_checks(conf, key_path=key, http=FakeServer()))["config"]
        assert config.ok is True and config.warning is False, config.line()


def test_the_old_agent_package_next_to_the_sdk_is_a_warning(monkeypatch):
    import importlib.metadata

    real = importlib.metadata.distribution

    def old_agent_installed(name, *args, **kwargs):
        return real("cairnml") if name == "cairnml-agent" else real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.metadata, "distribution", old_agent_installed)
    check = doctor.check_agent_package()
    assert check.ok is True and check.warning is True
    assert "pip uninstall cairnml-agent" in check.message
    assert check.line().startswith("warn")


def test_without_the_old_agent_package_the_check_passes(monkeypatch):
    import importlib.metadata

    real = importlib.metadata.distribution

    def no_old_agent(name, *args, **kwargs):
        if name == "cairnml-agent":
            raise importlib.metadata.PackageNotFoundError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.metadata, "distribution", no_old_agent)
    check = doctor.check_agent_package()
    assert check.ok is True and not check.warning
    assert check.line().startswith("ok")


def test_the_message_column_is_the_same_for_every_check_name():
    assert doctor.Check("config", True, "x").line().index("x") == 19
    assert doctor.Check("agent package", True, "x").line().index("x") == 19


@pytest.mark.parametrize("name", ["a" * 13, "a" * 14, "a" * 15, "a" * 20])
def test_a_name_longer_than_the_column_keeps_a_space_before_its_message(name):
    assert doctor.Check(name, True, "x").line() == f"ok   {name} x"


def test_every_line_of_a_healthy_run_has_its_message_in_the_same_column(tmp_path):
    conf = write_conf(tmp_path / "cairnml.conf")
    key = make_key(tmp_path / "device.key")
    checks = doctor.run_checks(conf, key_path=key, http=FakeServer())
    for check in checks:
        assert check.line()[18] == " " and check.line()[19:] == check.message, check.line()

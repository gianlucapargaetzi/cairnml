"""The device key helpers the agent gets from the SDK (secure_folder, save_device_key), and the assertion it signs."""

import os
import stat

import jwt
import pytest
from cairnml.idp.device import ASSERTION_TTL_SEC, save_device_key, secure_folder
from cairnml.idp.keys import generate_private_key, load_private_key, public_key_pem, read_private_key

from cairnml_agent.idp import device as agent_device


def test_secure_folder_creates_the_folder_and_its_parents_for_the_owner_only(tmp_path):
    folder = tmp_path / "a" / "b" / "agent"

    secure_folder(folder)

    assert folder.is_dir()
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700


def test_secure_folder_takes_a_string_and_can_run_again(tmp_path):
    folder = tmp_path / "agent"
    secure_folder(str(folder))
    os.chmod(folder, 0o755)

    secure_folder(str(folder))

    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700


def test_a_saved_device_key_is_a_pem_readable_by_its_owner_only(tmp_path):
    path = tmp_path / "agent" / "device.key"
    key = generate_private_key()

    save_device_key(path, key)

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert path.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")
    assert public_key_pem(load_private_key(path.read_bytes())) == public_key_pem(key)
    assert public_key_pem(read_private_key(path)) == public_key_pem(key)


def test_a_saved_device_key_never_replaces_an_existing_file(tmp_path):
    path = tmp_path / "device.key"
    first = generate_private_key()
    save_device_key(path, first)

    with pytest.raises(FileExistsError):
        save_device_key(path, generate_private_key())

    assert public_key_pem(read_private_key(path)) == public_key_pem(first)


def test_the_assertion_is_signed_with_the_key_it_is_given_for_sixty_seconds(tmp_path):
    path = tmp_path / "device.key"
    key = generate_private_key()
    save_device_key(path, key)

    assertion = agent_device.device_assertion("dev_1", "https://api.example", key_path=path)

    claims = jwt.decode(assertion, public_key_pem(key), algorithms=["EdDSA"], audience="https://api.example")
    assert claims["iss"] == "dev_1" and claims["sub"] == "dev_1"
    assert claims["exp"] - claims["iat"] == ASSERTION_TTL_SEC == 60


def test_the_assertion_reads_the_agents_own_key_by_default(tmp_path, monkeypatch):
    path = tmp_path / "agent.key"
    key = generate_private_key()
    save_device_key(path, key)
    monkeypatch.setattr(agent_device, "DEVICE_KEY_PATH", path)

    assertion = agent_device.device_assertion("dev_1", "https://api.example")

    jwt.decode(assertion, public_key_pem(key), algorithms=["EdDSA"], audience="https://api.example")


def test_the_agents_key_is_not_the_sdks_key():
    assert agent_device.DEVICE_KEY_PATH.parts[-3:] == (".cairnml", "agent", "device.key")

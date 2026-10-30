"""The device's key, and the short-lived assertion it signs to ask the server for a token."""

import os
from pathlib import Path

from .assertion import make_assertion
from .keys import read_private_key, write_private_key

# The agent has its own key, apart from the CairnML SDK's ~/.cairnml/device.key
DEVICE_KEY_PATH = Path.home() / ".cairnml" / "agent" / "device.key"
# Long enough to reach the server, short enough that a leaked assertion is useless soon after.
ASSERTION_TTL_SEC = 60


def secure_folder(folder) -> None:
    """Create the agent's folder, and make it accessible by its owner only."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)


def exposes_device_key(folder, key_path=None) -> bool:
    """True when mounting this host path into a job would give the job the device key: the key itself, its folder
    or any folder above them (such as the home folder or /)."""
    key = Path(key_path or DEVICE_KEY_PATH).expanduser().resolve()
    host = Path(folder).expanduser().resolve()
    return host == key or host in key.parents


def save_device_key(path, key) -> None:
    """Write a new device key, readable by its owner only."""
    write_private_key(path, key)


def device_assertion(device_id: str, audience: str, key_path=None) -> str:
    key = read_private_key(key_path or DEVICE_KEY_PATH)
    return make_assertion(key, device_id, audience, ttl=ASSERTION_TTL_SEC)

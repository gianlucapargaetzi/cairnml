"""The device's key, and the short-lived assertion it signs to ask the server for a token."""

from pathlib import Path

from .assertion import make_assertion
from .keys import read_private_key, write_private_key

DEVICE_KEY_PATH = Path.home() / ".clearml" / "device.key"
# Long enough to reach the server, short enough that a leaked assertion is useless soon after.
ASSERTION_TTL_SEC = 60


def save_device_key(path, key) -> None:
    """Write a new device key, readable by its owner only."""
    write_private_key(path, key)


def device_assertion(device_id: str, audience: str, key_path=None) -> str:
    key = read_private_key(key_path or DEVICE_KEY_PATH)
    return make_assertion(key, device_id, audience, ttl=ASSERTION_TTL_SEC)

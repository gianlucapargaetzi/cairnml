"""The agent's device key: its place, the check that a job cannot reach it, and the assertion it signs.

Writing the key (secure_folder, save_device_key) and signing are the SDK's (cairnml.idp.device); what stays here is
the agent's own key, apart from the SDK's ~/.cairnml/device.key, and the job-folder check, which only an agent needs.
"""

from pathlib import Path

from cairnml.idp.device import device_assertion as sdk_device_assertion

# The agent has its own key, apart from the CairnML SDK's ~/.cairnml/device.key
DEVICE_KEY_PATH = Path.home() / ".cairnml" / "agent" / "device.key"


def exposes_device_key(folder, key_path=None) -> bool:
    """True when mounting this host path into a job would give the job the device key: the key itself, its folder
    or any folder above them (such as the home folder or /)."""
    key = Path(key_path or DEVICE_KEY_PATH).expanduser().resolve()
    host = Path(folder).expanduser().resolve()
    return host == key or host in key.parents


def device_assertion(device_id: str, audience: str, key_path=None) -> str:
    """The SDK's signed assertion, with the agent's key unless another is given."""
    return sdk_device_assertion(device_id, audience, key_path or DEVICE_KEY_PATH)

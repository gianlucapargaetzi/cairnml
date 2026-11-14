"""The identity code in this package is copied from the CairnML SDK and the server (see #19).

The copies must stay byte-identical. The check runs when the sibling checkouts are present; CI runs without them and
skips. Set CAIRNML_SDK_IDP and CAIRNML_SERVER_IDP to point at other checkouts. The identity-drift CI job sets
CAIRNML_IDP_DRIFT_REQUIRED=1 and checks out the SDK and server main branches, so a missing checkout fails there.
"""

import hashlib
import os
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PACKAGE_IDP = HERE.parent / "cairnml_agent" / "idp"
SDK_IDP = Path(os.environ.get("CAIRNML_SDK_IDP", HERE.parent.parent / "cairnml" / "cairnml" / "idp"))
SERVER_IDP = Path(os.environ.get(
    "CAIRNML_SERVER_IDP", HERE.parent.parent / "cairnml-server" / "apiserver" / "idp"
))


REQUIRED = os.environ.get("CAIRNML_IDP_DRIFT_REQUIRED") == "1"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _missing_checkout(label, path):
    if REQUIRED:
        pytest.fail(f"{label} checkout not found at {path} (CAIRNML_IDP_DRIFT_REQUIRED=1)")
    pytest.skip(f"{label} checkout not found at {path}")


PACKAGE_FILES = sorted(path.name for path in PACKAGE_IDP.glob("*.py"))
# The files of idp/ that the agent deliberately changes, each with the reason. Any other file must be identical to its SDK copy.
MAY_DIFFER_FROM_SDK = {
    "device_login.py": "the agent adds choose_login() and _browser_available() for its own sign-in",
    "device.py": "the agent keeps its key under ~/.cairnml/agent and adds exposes_device_key()",
}


def test_every_file_that_may_differ_still_exists_in_the_agent():
    assert sorted(set(MAY_DIFFER_FROM_SDK) - set(PACKAGE_FILES)) == []


@pytest.mark.parametrize("name", PACKAGE_FILES)
def test_the_copy_matches_the_sdk(name):
    if not SDK_IDP.is_dir():
        _missing_checkout("SDK", SDK_IDP)
    theirs = SDK_IDP / name
    assert theirs.is_file(), f"{name} has no copy in the SDK"
    if name in MAY_DIFFER_FROM_SDK:
        return
    assert digest(PACKAGE_IDP / name) == digest(theirs), f"{name} differs from the SDK copy"


@pytest.mark.parametrize("name", ["assertion.py", "keys.py"])
def test_the_copy_matches_the_server(name):
    if not SERVER_IDP.is_dir():
        _missing_checkout("server", SERVER_IDP)
    assert digest(PACKAGE_IDP / name) == digest(SERVER_IDP / name), f"{name} differs from the server copy"

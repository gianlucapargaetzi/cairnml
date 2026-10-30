"""The identity code in this package is copied from the CairnML SDK and the server (see #19).

The copies must stay byte-identical. The check runs when the sibling checkouts are present; CI runs without them and
skips. Set CAIRNML_SDK_IDP and CAIRNML_SERVER_IDP to point at other checkouts.
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


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", ["assertion.py", "keys.py", "browser_login.py", "enrol.py"])
def test_the_copy_matches_the_sdk(name):
    if not SDK_IDP.is_dir():
        pytest.skip(f"SDK checkout not found at {SDK_IDP}")
    assert digest(PACKAGE_IDP / name) == digest(SDK_IDP / name), f"{name} differs from the SDK copy"


@pytest.mark.parametrize("name", ["assertion.py", "keys.py"])
def test_the_copy_matches_the_server(name):
    if not SERVER_IDP.is_dir():
        pytest.skip(f"server checkout not found at {SERVER_IDP}")
    assert digest(PACKAGE_IDP / name) == digest(SERVER_IDP / name), f"{name} differs from the server copy"

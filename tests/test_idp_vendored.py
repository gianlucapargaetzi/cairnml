import hashlib
import os
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cairnml.idp import assertion, keys

AUDIENCE = "https://api.example"
DEVICE = "dev_1234"
HERE = Path(__file__).resolve().parent
SERVER_IDP = Path(os.environ.get(
    "CAIRNML_SERVER_IDP", HERE.parent.parent / "cairnml-server" / "apiserver" / "idp"
))
AGENT_IDP = Path(os.environ.get(
    "CAIRNML_AGENT_IDP", HERE.parent.parent / "cairnml-agent" / "cairnml_agent" / "idp"
))
# The identity-drift CI job sets this: the sibling checkouts must exist there, so a missing one fails
# the job instead of skipping the comparison. Locally the comparison is skipped when a checkout is absent.
REQUIRED = os.environ.get("CAIRNML_IDP_DRIFT_REQUIRED") == "1"


def _missing_checkout(label, path):
    if REQUIRED:
        pytest.fail(f"{label} checkout not found at {path} (CAIRNML_IDP_DRIFT_REQUIRED=1)")
    pytest.skip(f"{label} checkout not found at {path}")


@pytest.fixture
def key():
    return Ed25519PrivateKey.generate()


def test_a_signed_assertion_verifies_for_its_audience(key):
    token = assertion.make_assertion(key, DEVICE, AUDIENCE)
    claims = assertion.verify_assertion(token, key.public_key(), AUDIENCE)
    assert claims["iss"] == DEVICE


def test_the_device_id_is_read_without_trusting_the_token(key):
    token = assertion.make_assertion(key, DEVICE, AUDIENCE)
    assert assertion.unverified_issuer(token) == DEVICE


def test_a_wrong_audience_is_refused(key):
    token = assertion.make_assertion(key, DEVICE, AUDIENCE)
    with pytest.raises(assertion.AssertionInvalid):
        assertion.verify_assertion(token, key.public_key(), "https://other.example")


def test_an_expired_assertion_is_refused(key):
    token = assertion.make_assertion(key, DEVICE, AUDIENCE, ttl=60, now=time.time() - 3600)
    with pytest.raises(assertion.AssertionInvalid, match="expired"):
        assertion.verify_assertion(token, key.public_key(), AUDIENCE)


def test_an_assertion_from_another_key_is_refused(key):
    token = assertion.make_assertion(key, DEVICE, AUDIENCE)
    other = Ed25519PrivateKey.generate().public_key()
    with pytest.raises(assertion.AssertionInvalid):
        assertion.verify_assertion(token, other, AUDIENCE)


def test_an_assertion_without_a_jti_is_refused(key):
    claims = {"iss": DEVICE, "sub": DEVICE, "aud": AUDIENCE, "iat": int(time.time()), "exp": int(time.time()) + 60}
    token = jwt.encode(claims, key, algorithm="EdDSA")
    with pytest.raises(assertion.AssertionInvalid):
        assertion.verify_assertion(token, key.public_key(), AUDIENCE)


def test_a_lifetime_longer_than_the_server_allows_is_refused(key):
    token = assertion.make_assertion(key, DEVICE, AUDIENCE, ttl=assertion.MAX_LIFETIME + 60)
    with pytest.raises(assertion.AssertionInvalid, match="lifetime"):
        assertion.verify_assertion(token, key.public_key(), AUDIENCE)


def test_a_key_survives_the_pem_round_trip(key):
    pem = keys.private_key_to_pem(key)
    again = keys.load_private_key(pem)
    token = assertion.make_assertion(again, DEVICE, AUDIENCE)
    assertion.verify_assertion(token, key.public_key(), AUDIENCE)


@pytest.mark.parametrize("name", ["assertion.py", "keys.py"])
def test_the_vendored_copy_matches_the_server(name):
    if not SERVER_IDP.is_dir():
        _missing_checkout("server", SERVER_IDP)
    ours = HERE.parent / "cairnml" / "idp" / name
    theirs = SERVER_IDP / name
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    assert digest(ours) == digest(theirs)


SDK_IDP_FILES = sorted(path.name for path in (HERE.parent / "cairnml" / "idp").glob("*.py"))
# The files of idp/ that the agent deliberately changes, each with the reason. Any other file must be identical to its agent copy.
MAY_DIFFER_FROM_AGENT = {
    "device_login.py": "the agent adds choose_login() and _browser_available() for its own sign-in",
    "device.py": "the agent keeps its key under ~/.cairnml/agent and adds exposes_device_key()",
}


def test_every_file_that_may_differ_still_exists_in_the_sdk():
    assert sorted(set(MAY_DIFFER_FROM_AGENT) - set(SDK_IDP_FILES)) == []


@pytest.mark.parametrize("name", SDK_IDP_FILES)
def test_the_copy_in_the_agent_matches_this_sdk(name):
    if not AGENT_IDP.is_dir():
        _missing_checkout("agent", AGENT_IDP)
    ours = HERE.parent / "cairnml" / "idp" / name
    theirs = AGENT_IDP / name
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    assert theirs.is_file(), f"{name} has no copy in the agent"
    if name in MAY_DIFFER_FROM_AGENT:
        return
    assert digest(ours) == digest(theirs), f"{name} differs from the agent copy"

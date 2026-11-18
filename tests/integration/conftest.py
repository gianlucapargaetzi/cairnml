"""Integration tests: the SDK against a real CairnML server.

Every test in this directory carries the ``integration`` marker. They run only when both variables are set:

* ``CAIRNML_TEST_SERVER``: the server's web address, for example ``http://localhost/cairnml``.
* ``CAIRNML_TEST_HOME``: a home directory holding a device enrolled on that server (``cairnml init --device`` run with
  ``HOME`` set to it).

The SDK reads its configuration and its device key from ``HOME`` when it is imported, so pytest itself must run with
``HOME`` set to ``CAIRNML_TEST_HOME``; the fixture below refuses to run otherwise rather than talk to the wrong server.
Without the two variables the tests are reported as skipped, so an unmarked ``pytest`` still runs without a server.

Run them with::

    HOME=$CAIRNML_TEST_HOME CAIRNML_TEST_SERVER=http://localhost/cairnml CAIRNML_TEST_HOME=$CAIRNML_TEST_HOME \\
        python -m pytest -m integration tests/integration

A new test module here needs only ``pytestmark = pytest.mark.integration`` and the ``real_server`` fixture (which
every test in the directory already uses through ``autouse``).
"""

import os
from pathlib import Path

import pytest

SERVER_VARIABLE = "CAIRNML_TEST_SERVER"
HOME_VARIABLE = "CAIRNML_TEST_HOME"


def _same_address(a: str, b: str) -> bool:
    return a.rstrip("/").lower() == b.rstrip("/").lower()


@pytest.fixture(scope="session", autouse=True)
def real_server() -> str:
    """The web address of the server the tests run against. Skips the directory when the environment names none."""
    server = os.environ.get(SERVER_VARIABLE, "").strip()
    home = os.environ.get(HOME_VARIABLE, "").strip()
    if not server or not home:
        pytest.skip(
            f"needs {SERVER_VARIABLE} (the server's web address) and {HOME_VARIABLE} (a home with an enrolled device); "
            "see CONTRIBUTING.md, section Integration tests"
        )
    if Path(home).resolve() != Path(os.path.expanduser("~")).resolve():
        pytest.fail(
            f"{HOME_VARIABLE} is {home} but HOME is {os.path.expanduser('~')}: run pytest with HOME set to {home}, "
            "so that the SDK reads the enrolled device",
            pytrace=False,
        )
    from cairnml.backend_api import Session

    configured = Session.get_app_server_host()
    if not _same_address(configured, server):
        pytest.fail(
            f"the enrolled home is for {configured}, but {SERVER_VARIABLE} is {server}: enrol the home on that server",
            pytrace=False,
        )
    return server

"""Integration tests: the agent against a real CairnML server.

Every test in this directory carries the ``integration`` marker. They run only when these three variables are set:

* ``CAIRNML_TEST_SERVER``: the server's web address, for example ``http://localhost/cairnml``.
* ``CAIRNML_TEST_ADMIN_USER`` and ``CAIRNML_TEST_ADMIN_PASSWORD``: the sign-in of a CairnML admin. Only an admin may
  enrol an agent, and the tests enrol their own scratch homes, so nothing is read from the real ~/.cairnml.

Without them the tests are reported as skipped, so an unmarked ``pytest`` still runs without a server. The password is
never printed. The step-by-step run is in CONTRIBUTING.md, section Integration tests.
"""

import os

import pytest

SERVER_VARIABLE = "CAIRNML_TEST_SERVER"
USER_VARIABLE = "CAIRNML_TEST_ADMIN_USER"
PASSWORD_VARIABLE = "CAIRNML_TEST_ADMIN_PASSWORD"


@pytest.fixture(scope="session", autouse=True)
def real_server() -> dict:
    """The server and the admin sign-in the tests use. Skips the directory when the environment names none."""
    server = os.environ.get(SERVER_VARIABLE, "").strip()
    user = os.environ.get(USER_VARIABLE, "").strip()
    password = os.environ.get(PASSWORD_VARIABLE, "").strip()
    if not (server and user and password):
        pytest.skip(
            f"needs {SERVER_VARIABLE} (the server's web address), {USER_VARIABLE} and {PASSWORD_VARIABLE} "
            "(an admin's sign-in); see CONTRIBUTING.md, section Integration tests"
        )
    return {"server": server.rstrip("/"), "user": user, "password": password}

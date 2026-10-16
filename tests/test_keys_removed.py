import inspect
import os
import subprocess
import sys

import clearml
from clearml import Task
from clearml.backend_api.session import Session
from clearml.backend_api.session import defs


def test_the_session_takes_no_access_key_arguments():
    params = inspect.signature(Session.__init__).parameters
    assert "api_key" not in params
    assert "secret_key" not in params


def test_the_access_key_environment_entries_are_gone():
    assert not hasattr(defs, "ENV_ACCESS_KEY")
    assert not hasattr(defs, "ENV_SECRET_KEY")


def test_the_credential_browser_login_is_gone():
    assert not hasattr(clearml, "browser_login")


def test_task_set_credentials_takes_no_keys():
    params = inspect.signature(Task.set_credentials).parameters
    assert "key" not in params
    assert "secret" not in params


def test_access_keys_in_the_environment_do_not_sign_a_session_in(tmp_path):
    env = dict(os.environ)
    # An empty home: the machine's own configuration must not be read by this test.
    env["HOME"] = str(tmp_path)
    env["CLEARML_CONFIG_FILE"] = str(tmp_path / "none.conf")
    env.update({
        "CLEARML_API_ACCESS_KEY": "a-key",
        "CLEARML_API_SECRET_KEY": "a-secret",
        "CLEARML_API_HOST": "http://127.0.0.1:9",
    })
    env.pop("CLEARML_AUTH_TOKEN", None)
    env.pop("CLEARML_JOB_TOKEN", None)
    env.pop("CLEARML_CONFIG_FILE", None)
    code = "from clearml.backend_api.session import Session; Session()"
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode != 0
    assert "MissingConfigError" in result.stderr or "MissingConfigError" in result.stdout

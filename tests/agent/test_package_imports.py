"""The agent's modules import the names they use from the modules that define them, and the package imports cleanly."""

import subprocess
import sys
from pathlib import Path

from cairnml_agent.backend_api.session.callresult import CallResult
from cairnml_agent.backend_api.session.client.client import APIClient
from cairnml_agent.backend_api.session.request import Request
from cairnml_agent.backend_api.session.session import Session
from cairnml_agent.backend_config.config import Config

ROOT = Path(__file__).resolve().parent.parent


def test_the_names_are_defined_in_their_own_modules():
    assert all(isinstance(name, type) for name in (CallResult, APIClient, Request, Session, Config))


def test_the_package_imports_without_the_working_directory_on_the_path():
    code = (
        "import sys; sys.path.insert(0, {!r}); "
        "import cairnml_agent, cairnml_agent.commands, cairnml_agent.interface; print('ok')"
    ).format(str(ROOT))
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_the_packages_export_only_the_names_their_importers_use():
    import cairnml_agent
    import cairnml_agent.backend_api as backend_api
    import cairnml_agent.backend_api.session as session
    import cairnml_agent.backend_api.session.client as client
    import cairnml_agent.backend_config as backend_config
    import cairnml_agent.commands as commands

    assert set(backend_api.__all__) == {"CallResult"}
    assert set(client.__all__) == {"APIClient", "APIError"}
    assert set(backend_config.__all__) == {"Config"}
    assert set(commands.__all__) == {"Worker", "install_bootstrap"}
    assert not hasattr(session, "TokenManager")
    assert not hasattr(session, "TimeoutExpiredError")
    assert not hasattr(backend_api, "load_config")
    assert not hasattr(cairnml_agent, "APIClient")

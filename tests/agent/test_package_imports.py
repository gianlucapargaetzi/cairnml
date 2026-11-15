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


def test_the_packages_export_only_the_names_that_are_used_through_them():
    import cairnml_agent
    import cairnml_agent.backend_api as backend_api
    import cairnml_agent.backend_api.session as session
    import cairnml_agent.backend_api.session.client as client
    import cairnml_agent.backend_config as backend_config
    import cairnml_agent.commands as commands

    # the generated services import these eight names from the session package
    assert set(session.__all__) == {
        "DataModel", "NonStrictDataModel", "schema_property", "StringEnum",
        "Request", "BatchRequest", "CompoundRequest", "Response",
    }
    # __main__ looks these up by name on the commands package
    assert set(commands.__all__) == {"Worker", "Config", "install_bootstrap"}
    for module, name in [(session, "Session"), (session, "CallResult"), (session, "TokenManager"),
                         (client, "APIClient"), (backend_api, "CallResult"),
                         (backend_config, "Config"), (cairnml_agent, "APIClient")]:
        assert not hasattr(module, name), "{}.{} is still re-exported".format(module.__name__, name)


# Modules that fail to import and are tolerated for now: none. A module that fails and is not listed fails the test, and a listed
# module that imports again fails it too, because the list is then stale.
KNOWN_IMPORT_FAILURES = set()


def test_every_module_of_the_agent_imports_except_the_known_failures():
    code = (
        "import sys, importlib, pkgutil; sys.path.insert(0, {root!r}); import cairnml_agent\n"
        "failed = []\n"
        "for m in pkgutil.walk_packages(cairnml_agent.__path__, 'cairnml_agent.'):\n"
        "    if any(part in m.name for part in ('_vendor', '.external', 'services', '__main__')):\n"
        "        continue\n"
        "    try:\n"
        "        importlib.import_module(m.name)\n"
        "    except BaseException:\n"
        "        failed.append(m.name)\n"
        "print('\\n'.join(sorted(failed)))\n"
    ).format(root=str(ROOT))
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert set(result.stdout.split()) == KNOWN_IMPORT_FAILURES


def test_the_embedded_pyhocon_jsonmodels_and_pynvml_copies_are_gone():
    import importlib.util

    for name in (
        "cairnml_agent.external.pyhocon",
        "cairnml_agent.backend_api.session.jsonmodels",
        "cairnml_agent.helper.gpu.pynvml",
    ):
        assert importlib.util.find_spec(name) is None, name

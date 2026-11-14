"""The public surface of the package names nothing the CairnML server cannot serve.

HyperDatasets (datasets, dataviews and frames services) has no counterpart on the server, so none of its names are exported, none of
its modules are loaded by ``import cairnml``, and the service proxies do not name it.
"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import cairnml

REMOVED_NAMES = (
    "DataEntry",
    "DataSubEntry",
    "DataEntryImage",
    "DataSubEntryImage",
    "HyperDataset",
    "HyperDatasetManagement",
    "DataView",
    "HyperDatasetQuery",
)


def test_every_name_in_all_is_importable():
    missing = [name for name in cairnml.__all__ if not hasattr(cairnml, name)]
    assert missing == []


def test_no_hyperdataset_name_is_exported():
    still_exported = [name for name in REMOVED_NAMES if name in cairnml.__all__ or hasattr(cairnml, name)]
    assert still_exported == []


def test_import_cairnml_loads_no_hyperdataset_module():
    code = (
        "import sys, cairnml; "
        "print([m for m in sys.modules if m.startswith(('cairnml.hyperdatasets', 'cairnml.backend_interface.datasets'))])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"


def test_the_service_proxies_name_no_dataset_service():
    from cairnml.backend_api import services

    named = [name for name in ("datasets", "dataviews", "frames") if name in services.__all__ or hasattr(services, name)]
    assert named == []


def test_the_task_has_no_dataview_helpers():
    assert not hasattr(cairnml.Task, "set_dataview")
    assert not hasattr(cairnml.Task, "get_dataviews")


def test_the_default_configuration_has_no_dataview_setting():
    from cairnml.utilities.pyhocon import ConfigFactory

    default = Path(cairnml.__file__).parent / "config" / "default" / "sdk.conf"
    parsed = ConfigFactory.parse_file(str(default))
    assert "store_dataviews_on_creation" not in parsed.get("development")


def test_a_cleared_task_sends_no_empty_dataviews_list(monkeypatch):
    from cairnml.backend_api.services.v2_23 import tasks as generated_tasks
    from cairnml.backend_interface.task import task as task_module

    monkeypatch.setattr(task_module, "tasks", generated_tasks)
    monkeypatch.setattr(task_module.Session, "check_min_api_version", staticmethod(lambda version: version == "2.3"))
    sent = {}
    fake = SimpleNamespace(
        _data=SimpleNamespace(output=SimpleNamespace(), system_tags=None),
        _set_task_property=lambda *args, **kwargs: None,
        _edit=lambda **kwargs: sent.update(kwargs),
    )
    task_module.Task._clear_task(fake, comment="c")
    assert "dataviews" not in sent["execution"].to_dict()

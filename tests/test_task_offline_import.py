from types import SimpleNamespace

from cairnml import Task
from cairnml.backend_api.session import Session


class _EditResult:
    def ok(self):
        return True


class _FakeTask:
    id = "fake-task-id"

    def __init__(self):
        self.edits = None
        self.data = SimpleNamespace(to_dict=lambda: {})
        self.session = SimpleNamespace(api_version="2.41")

    def reload(self):
        pass

    def _edit(self, **kwargs):
        self.edits = kwargs
        return _EditResult()


def test_an_export_written_before_the_change_imports_and_the_old_version_key_is_ignored(monkeypatch):
    fake = _FakeTask()
    monkeypatch.setattr(Task, "create", lambda **kwargs: fake)
    api_version = Session.api_version

    task_data = {
        "name": "exported-run",
        "project_name": "examples",
        "session_api_version": "2.9",
    }
    imported = Task.import_task(task_data)

    assert imported is fake
    assert fake.edits is not None
    assert fake.edits["name"] == "exported-run"
    assert "session_api_version" not in fake.edits
    assert Session.api_version == api_version


def test_an_export_no_longer_records_the_session_api_version(monkeypatch):
    fake = _FakeTask()
    fake.get_project_name = lambda: "examples"
    fake.data = SimpleNamespace(to_dict=lambda: {"name": "exported-run"})
    fake.reload = lambda: None

    exported = Task.export_task(fake)

    assert exported["name"] == "exported-run"
    assert exported["project_name"] == "examples"
    assert "session_api_version" not in exported

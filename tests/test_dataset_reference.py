"""A dataset is named by its id, its name or its repository address. The server resolves it; a local folder is read as it
is, and a repository address is read by its address when the server cannot be asked."""
import pytest

from cairnml.datasets import dataset as dataset_module
from cairnml.datasets.errors import DatasetError


def test_a_name_resolves_to_its_repository_id_and_reference(monkeypatch):
    seen = []

    def resolve(reference, task=None):
        seen.append((reference, task))
        return {"id": "d1", "kind": "repo", "ref": "acme/cars", "reference": "cars"}

    monkeypatch.setattr(dataset_module, "resolve_dataset", resolve)
    monkeypatch.setenv("CAIRNML_TASK_ID", "task-7")
    assert dataset_module._resolve_reference("cars") == ("https://github.com/acme/cars", "d1", "cars")
    assert seen == [("cars", "task-7")]


def test_a_registry_entry_of_a_repository_resolves_to_that_repository(monkeypatch):
    monkeypatch.setattr(dataset_module, "resolve_dataset", lambda reference, task=None: {
        "id": "e1", "kind": "registry_entry", "source": "repo", "link": "https://github.com/acme/coco",
        "ref": "owner/registry/coco", "reference": "registry/coco"})
    assert dataset_module._resolve_reference("registry/coco") == ("https://github.com/acme/coco", "e1", "registry/coco")


def test_a_local_folder_is_read_without_the_server(monkeypatch, tmp_path):
    def must_not_ask(reference, task=None):
        raise AssertionError("the server was asked for a local folder")

    monkeypatch.setattr(dataset_module, "resolve_dataset", must_not_ask)
    assert dataset_module._resolve_reference(str(tmp_path)) == (str(tmp_path), None, None)


def test_a_repository_address_is_read_by_its_address_when_the_server_cannot_be_asked(monkeypatch):
    def unreachable(reference, task=None):
        raise DatasetError("no login")

    monkeypatch.setattr(dataset_module, "resolve_dataset", unreachable)
    assert dataset_module._resolve_reference("https://github.com/acme/cars.git") == (
        "https://github.com/acme/cars.git", None, None)


def test_a_name_the_server_does_not_know_is_an_error(monkeypatch):
    monkeypatch.setattr(dataset_module, "resolve_dataset", lambda reference, task=None: {})
    with pytest.raises(DatasetError, match="no dataset unknown"):
        dataset_module._resolve_reference("unknown")

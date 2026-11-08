"""``cairnml dataset clone``: the server answers with the address, the commit and the read token; the repository is cloned
with the token for one command, the commit checked out, and the data copied into data/."""
import subprocess
from types import SimpleNamespace

import pytest

from cairnml.datasets import clone as clone_module
from cairnml.datasets.errors import DatasetError


class Answer:
    def __init__(self, data, ok=True):
        self._data = data
        self.ok = ok
        self.text = ""

    def json(self):
        return {"data": self._data}


def test_a_public_dataset_is_cloned_without_a_token(tmp_path, monkeypatch):
    bare = tmp_path / "server.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    seen = []
    monkeypatch.setattr(clone_module, "_person_request", lambda session, action, body: (
        seen.append((action, body)) or Answer({"repository": str(bare), "token": None, "commit": None,
                                               "dataset": {"name": "cars"}})))
    data_folder = tmp_path / "cache" / "data"
    data_folder.mkdir(parents=True)
    (data_folder / "a.txt").write_text("alpha")
    monkeypatch.setattr(clone_module.Dataset, "get", classmethod(lambda cls, ref, commit, record=True: SimpleNamespace(
        path=data_folder)))
    target = clone_module.clone_dataset("cars", path=str(tmp_path / "work"))
    assert seen == [("clone", {"ref": "cars"})]
    assert (target / "data" / "a.txt").read_text() == "alpha"


def test_a_folder_that_is_not_empty_is_refused(tmp_path, monkeypatch):
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "x").write_text("x")
    monkeypatch.setattr(clone_module, "_person_request", lambda *a: Answer({"repository": "https://x/y.git"}))
    with pytest.raises(DatasetError, match="not empty"):
        clone_module.clone_dataset("cars", path=str(tmp_path / "work"))


def test_a_server_refusal_is_said_as_it_is(monkeypatch, tmp_path):
    monkeypatch.setattr(clone_module, "_person_request", lambda *a: Answer({}, ok=False))
    with pytest.raises(DatasetError, match="could not clone"):
        clone_module.clone_dataset("cars", path=str(tmp_path / "work"))

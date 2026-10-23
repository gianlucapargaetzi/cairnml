"""External datasets: pin file, adapter choice, canonical format, caching, and the errors that name the adapter."""

import json
import shutil
import subprocess

import pytest

from clearml.datasets import Dataset, DatasetCache, DatasetError
from clearml.datasets.schema import SchemaError, validate_rows
from clearml.datasets.sources import HuggingFace, LocalFolder

ADAPTER_V1 = '''
import json
from clearml.datasets import Adapter

class Toy(Adapter):
    modalities = {"image": "image"}
    labels = {"bbox2d": "bbox2d"}

    def run(self, source, writer):
        for item in json.loads((source / "labels.json").read_text()):
            boxes = [{"box": b, "label": c} for b, c in item["boxes"]]
            writer.add(item["id"], item["split"], {"image": item["file"]}, {"bbox2d": boxes})
'''

# The source changed its layout: boxes became [x, y, width, height].
ADAPTER_V2 = '''
import json
from clearml.datasets import Adapter

class ToyXywh(Adapter):
    modalities = {"image": "image"}
    labels = {"bbox2d": "bbox2d"}

    def run(self, source, writer):
        for item in json.loads((source / "labels.json").read_text()):
            boxes = [{"box": [x, y, x + w, y + h], "label": c} for (x, y, w, h), c in item["boxes"]]
            writer.add(item["id"], item["split"], {"image": item["file"]}, {"bbox2d": boxes})
'''


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for name, value in (("NAME", "Tester"), ("EMAIL", "tester@example.com")):
        monkeypatch.setenv(f"GIT_AUTHOR_{name}", value)
        monkeypatch.setenv(f"GIT_COMMITTER_{name}", value)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "a.png").write_bytes(b"a")
    (root / "b.png").write_bytes(b"b")
    (root / "labels.json").write_text(json.dumps([
        {"id": "a", "split": "train", "file": "a.png", "boxes": [[[1, 2, 10, 20], "car"]]},
        {"id": "b", "split": "val", "file": "b.png", "boxes": []},
    ]))
    return root


def pin(source, overrides=""):
    return f"source: local\nlink: {source}\nadapter: toy_v1\n{overrides}"


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def external(tmp_path, source):
    """An external-datasets repository with one dataset, returned as (origin url, first commit)."""
    origin = tmp_path / "server" / "acme" / "external-datasets.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    work = tmp_path / "external-work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    (work / "datasets").mkdir()
    (work / "adapters").mkdir()
    (work / "datasets" / "toy.yaml").write_text(pin(source))
    (work / "adapters" / "toy_v1.py").write_text(ADAPTER_V1)
    (work / "adapters" / "toy_v2.py").write_text(ADAPTER_V2)
    git(work, "add", "-A")
    git(work, "commit", "-qm", "toy")
    git(work, "push", "-q", "origin", "HEAD")
    return {"url": str(origin), "work": work, "commit": git(work, "rev-parse", "HEAD")}


@pytest.fixture
def cache(tmp_path):
    return DatasetCache(tmp_path / "cache", max_bytes=10**9)


def test_an_external_dataset_is_read_in_the_canonical_format(external, cache, source):
    data = Dataset.get_external("toy", external["commit"], repo=external["url"], cache=cache)
    samples = list(data.samples())
    assert [s.id for s in samples] == ["a", "b"]
    assert samples[0].split == "train"
    assert samples[0].modality("image") == source / "a.png"
    assert samples[0].label("bbox2d") == [{"box": [1.0, 2.0, 10.0, 20.0], "label": "car"}]
    assert samples[1].label("bbox2d") == []
    assert [s.id for s in data.samples(split="val")] == ["b"]
    assert data.adapter == "toy_v1" and data.repo_commit == external["commit"]
    assert data.revision == LocalFolder().resolve(str(source), None)


def test_the_same_revision_is_not_read_again(external, cache, monkeypatch):
    Dataset.get_external("toy", external["commit"], repo=external["url"], cache=cache)
    from clearml.datasets import external as module

    def fail(*_a, **_k):
        raise AssertionError("adapter ran again")

    monkeypatch.setattr(module, "_build", fail)
    assert len(Dataset.get_external("toy", external["commit"], repo=external["url"], cache=cache)) == 2


def test_a_changed_layout_fails_with_the_adapter_named(external, cache, source):
    (source / "labels.json").write_text(json.dumps([
        {"id": "a", "split": "train", "file": "a.png", "boxes": [[[10, 20, 5, 5], "car"]]},
    ]))
    with pytest.raises(DatasetError) as error:
        Dataset.get_external("toy", external["commit"], repo=external["url"], cache=cache)
    assert "toy_v1" in str(error.value) and "x2 > x1" in str(error.value)


def test_an_override_selects_another_adapter_for_a_revision(external, cache, source):
    (source / "labels.json").write_text(json.dumps([
        {"id": "a", "split": "train", "file": "a.png", "boxes": [[[10, 20, 5, 5], "car"]]},
    ]))
    changed = LocalFolder().resolve(str(source), None)
    work = external["work"]
    (work / "datasets" / "toy.yaml").write_text(
        pin(source, f"overrides:\n  - revisions: ['{changed[:8]}']\n    adapter: toy_v2\n")
    )
    git(work, "commit", "-qam", "adapter for the new layout")
    git(work, "push", "-q", "origin", "HEAD")
    data = Dataset.get_external("toy", git(work, "rev-parse", "HEAD"), repo=external["url"], cache=cache)
    assert data.adapter == "toy_v2"
    assert next(data.samples()).label("bbox2d")[0]["box"] == [10.0, 20.0, 15.0, 25.0]


def test_the_older_commit_still_reads_the_older_revision(external, cache, source):
    first = Dataset.get_external("toy", external["commit"], repo=external["url"], cache=cache)
    (source / "extra.txt").write_text("changes the content hash")
    second = Dataset.get_external("toy", external["commit"], repo=external["url"], cache=cache)
    assert first.revision != second.revision


def test_an_unknown_dataset_lists_the_known_ones(external, cache):
    with pytest.raises(DatasetError, match="known: toy"):
        Dataset.get_external("nope", external["commit"], repo=external["url"], cache=cache)


def test_a_commit_that_is_not_pushed_is_refused(external, cache):
    with pytest.raises(DatasetError, match="is not on"):
        Dataset.get_external("toy", "0" * 40, repo=external["url"], cache=cache)


def test_a_failed_read_leaves_nothing_in_the_cache(external, cache, source):
    (source / "labels.json").write_text("not json")
    with pytest.raises(DatasetError):
        Dataset.get_external("toy", external["commit"], repo=external["url"], cache=cache)
    assert not any(p.name.startswith("ext__") for p in (cache.root / "checkouts").iterdir())


class TestSchema:
    SPEC = ({"image": "image"}, {"bbox2d": "bbox2d"})

    def rows(self, **overrides):
        row = {"sample_id": "a", "split": "train", "modality.image": "a.png", "label.bbox2d": []}
        row.update(overrides)
        return [row]

    def test_a_valid_sample_passes(self):
        validate_rows(self.rows(), *self.SPEC)

    def test_duplicate_ids_are_refused(self):
        with pytest.raises(SchemaError, match="twice"):
            validate_rows(self.rows() + self.rows(), *self.SPEC)

    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(SchemaError, match="does not exist"):
            validate_rows(self.rows(), *self.SPEC, root=tmp_path)

    def test_a_box_in_xywh_is_refused(self):
        bad = [{"box": [10, 10, 5, 5], "label": "car"}]
        with pytest.raises(SchemaError, match="x2 > x1"):
            validate_rows(self.rows(**{"label.bbox2d": bad}), *self.SPEC)


class TestSources:
    def test_hugging_face_links_are_read_in_both_forms(self):
        for link in ("hf://datasets/org/name", "https://huggingface.co/datasets/org/name", "org/name"):
            assert HuggingFace._repo_id(link) == "org/name"

    def test_a_full_sha_is_used_as_it_is(self):
        sha = "a" * 40
        assert HuggingFace().resolve("org/name", sha) == sha

    def test_a_local_folder_must_still_match_a_requested_hash(self, tmp_path):
        (tmp_path / "f").write_text("1")
        wanted = LocalFolder().resolve(str(tmp_path), None)
        (tmp_path / "f").write_text("2")
        with pytest.raises(DatasetError, match="no longer matches"):
            LocalFolder().resolve(str(tmp_path), wanted)


def test_a_local_link_can_point_into_the_external_datasets_repository(external, cache, source):
    work = external["work"]
    shutil.copytree(source, work / "data" / "toy")
    (work / "datasets" / "inside.yaml").write_text("source: local\nlink: data/toy\nadapter: toy_v1\n")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "inside")
    git(work, "push", "-q", "origin", "HEAD")
    data = Dataset.get_external("inside", git(work, "rev-parse", "HEAD"), repo=external["url"], cache=cache)
    assert len(data) == 2
    assert next(data.samples()).modality("image").read_bytes() == b"a"


def test_without_a_commit_the_latest_one_is_used_and_recorded(external, cache, source):
    work = external["work"]
    (work / "datasets" / "toy.yaml").write_text(pin(source) + "# touched\n")
    git(work, "commit", "-qam", "newer")
    git(work, "push", "-q", "origin", "HEAD")
    latest = git(work, "rev-parse", "HEAD")
    assert latest != external["commit"]
    data = Dataset.get_external("toy", repo=external["url"], cache=cache)
    assert data.repo_commit == latest
    assert Dataset.get_external("toy", external["commit"], repo=external["url"], cache=cache).repo_commit == external["commit"]

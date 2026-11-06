"""External datasets: pin file, adapter choice, canonical format, caching, and the errors that name the adapter."""

import importlib.metadata
import json
import shutil
import subprocess
import types

import pytest

from cairnml.datasets import Dataset, DatasetCache, DatasetError, SampleWriter
from cairnml.datasets.external import Pin, _ensure_source, adapter_requirements, requirements_digest
from cairnml.datasets.schema import SchemaError, validate_rows
from cairnml.datasets.sources import LocalFolder

ADAPTER_V1 = '''
import json
from cairnml.datasets import Adapter

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
from cairnml.datasets import Adapter

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
    from cairnml.datasets import external as module

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


class TestTabularLabels:
    SPEC = ({"table": "table"}, {"features": "tabular", "energy": "number"})

    def rows(self, **overrides):
        row = {
            "sample_id": "a", "split": "train", "modality.table": "a.csv",
            "label.features": {"temp": 21.5, "plant": "tomato"}, "label.energy": 12.3,
        }
        row.update(overrides)
        return [row]

    def test_a_valid_tabular_sample_passes(self):
        validate_rows(self.rows(), *self.SPEC)

    @pytest.mark.parametrize("bad", [[], "temp=1", {}, {"": 1.0}, {"temp": True}, {"temp": float("nan")}, {"temp": [1]}])
    def test_a_tabular_value_must_be_named_numbers_or_text(self, bad):
        with pytest.raises(SchemaError, match="features"):
            validate_rows(self.rows(**{"label.features": bad}), *self.SPEC)

    def test_every_sample_has_the_same_columns(self):
        second = {"sample_id": "b", "split": "val", "modality.table": "b.csv", "label.features": {"temp": 1.0}}
        with pytest.raises(SchemaError, match="first sample has"):
            validate_rows(self.rows() + [second], *self.SPEC)

    def test_a_number_label_must_be_finite(self):
        with pytest.raises(SchemaError, match="finite number"):
            validate_rows(self.rows(**{"label.energy": float("inf")}), *self.SPEC)
        with pytest.raises(SchemaError, match="finite number"):
            validate_rows(self.rows(**{"label.energy": "12.3"}), *self.SPEC)

    def test_tabular_and_number_labels_round_trip_through_the_table(self, tmp_path):
        from cairnml.datasets.external import ExternalDataset

        (tmp_path / "a.csv").write_text("x\n1\n")
        writer = SampleWriter(tmp_path, *self.SPEC, source_root=tmp_path)
        writer.add("a", "train", {"table": "a.csv"}, {"features": {"temp": 21, "plant": "tomato"}, "energy": 12.3})
        writer.add("b", "val", {"table": "a.csv"}, {"features": {"temp": 2.5, "plant": "basil"}, "energy": 0.0})
        writer.close({"dataset": "greenhouse"})
        data = ExternalDataset({"name": "greenhouse"}, tmp_path, tmp_path)
        first, second = list(data.samples())
        assert first.label("features") == {"temp": 21.0, "plant": "tomato"}
        assert second.label("energy") == 0.0 and second.label("features")["plant"] == "basil"


class TestSources:
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


# A source that is fetched by the adapter: ``fetch`` copies the folder named by the link, ``resolve`` is the revision.
def remote_adapter(requirements=(), fetch=True):
    head = f"REQUIREMENTS = {list(requirements)!r}\nimport shutil\n"
    head += "def resolve(link, revision):\n    return revision or 'rev-1'\n"
    if fetch:
        head += "def fetch(link, revision, dest):\n    shutil.copytree(link, dest)\n"
    return head + ADAPTER_V1


def publish(external, files):
    """Commit files to the external-datasets repository and push. Returns the new commit."""
    work = external["work"]
    for path, text in files.items():
        (work / path).write_text(text)
    git(work, "add", "-A")
    git(work, "commit", "-qm", "change")
    git(work, "push", "-q", "origin", "HEAD")
    return git(work, "rev-parse", "HEAD")


def remote_pin(source, adapter="remote_v1"):
    return f"source: remote\nlink: {source}\nadapter: {adapter}\n"


def test_a_source_that_is_not_local_is_fetched_by_its_adapter(external, cache, source):
    commit = publish(external, {
        "datasets/remote.yaml": remote_pin(source),
        "adapters/remote_v1.py": remote_adapter(),
    })
    data = Dataset.get_external("remote", commit, repo=external["url"], cache=cache)
    assert data.source == "remote" and data.revision == "rev-1"
    assert [s.id for s in data.samples()] == ["a", "b"]
    # The files were copied by the adapter's fetch into the cache, not read from the folder they came from.
    assert data.root.is_relative_to(cache.root) and data.root != source
    assert (data.root / "a.png").read_bytes() == b"a"


def test_a_source_without_a_fetch_in_its_adapter_is_refused(external, cache, source):
    commit = publish(external, {
        "datasets/remote.yaml": remote_pin(source, adapter="toy_v1"),
    })
    with pytest.raises(DatasetError, match="adapter 'toy_v1' has no fetch for source 'remote'"):
        Dataset.get_external("remote", commit, revision="rev-1", repo=external["url"], cache=cache)


def test_a_missing_requirement_is_refused_with_the_install_command(external, cache, source):
    commit = publish(external, {
        "datasets/remote.yaml": remote_pin(source),
        "adapters/remote_v1.py": remote_adapter(["cairnml-no-such-package==1.0"]),
    })
    with pytest.raises(DatasetError) as error:
        Dataset.get_external("remote", commit, repo=external["url"], cache=cache)
    assert 'pip install "cairnml-no-such-package==1.0"' in str(error.value)
    assert "cairnml-no-such-package 1.0" in str(error.value)


def test_a_requirement_at_another_version_is_refused_with_the_install_command(external, cache, source):
    installed = importlib.metadata.version("pytest")
    wanted = "0.0.1" if installed != "0.0.1" else "0.0.2"
    commit = publish(external, {
        "datasets/remote.yaml": remote_pin(source),
        "adapters/remote_v1.py": remote_adapter([f"pytest=={wanted}"]),
    })
    with pytest.raises(DatasetError) as error:
        Dataset.get_external("remote", commit, repo=external["url"], cache=cache)
    assert f"pytest is {installed}" in str(error.value)
    assert f'pip install "pytest=={wanted}"' in str(error.value)


def test_an_installed_requirement_at_its_exact_version_is_accepted(external, cache, source):
    installed = importlib.metadata.version("pytest")
    commit = publish(external, {
        "datasets/remote.yaml": remote_pin(source),
        "adapters/remote_v1.py": remote_adapter([f"pytest=={installed}"]),
    })
    assert len(Dataset.get_external("remote", commit, repo=external["url"], cache=cache)) == 2


def test_a_requirement_must_be_an_exact_pin():
    module = types.ModuleType("adapter")
    module.REQUIREMENTS = ["pytest>=1"]
    with pytest.raises(DatasetError, match="must be an exact pin"):
        adapter_requirements(module, "toy")
    assert adapter_requirements(types.ModuleType("none"), "toy") == []


def test_the_cache_key_changes_when_the_requirements_change():
    assert requirements_digest([]) != requirements_digest(["pytest==9.1.1"])
    assert requirements_digest(["pytest==9.1.1"]) != requirements_digest(["pytest==9.1.1", "six==1.16.0"])
    assert requirements_digest(["pytest==9.1.1", "six==1.16.0"]) == requirements_digest(["pytest==9.1.1", "six==1.16.0"])


def test_the_built_output_is_keyed_by_the_requirements(external, cache, source):
    installed = importlib.metadata.version("pytest")
    commit = publish(external, {
        "datasets/remote.yaml": remote_pin(source),
        "adapters/remote_v1.py": remote_adapter([f"pytest=={installed}"]),
    })
    data = Dataset.get_external("remote", commit, repo=external["url"], cache=cache)
    assert data.path.name.endswith(requirements_digest([f"pytest=={installed}"]))
    assert data.path.name != f"{commit[:12]}-{data.revision[:12]}-{requirements_digest([])}"


def test_a_fetch_runs_once_per_revision_and_a_failed_one_leaves_nothing(cache):
    calls = []

    def fetch(link, revision, dest):
        calls.append((link, revision))
        dest.mkdir(parents=True)
        (dest / "f.txt").write_text(link)

    def broken(link, revision, dest):
        dest.mkdir(parents=True)
        raise OSError("network down")

    def pin(link):
        return Pin(name="remote", source="remote", adapter="remote_v1", link=link)

    url = "https://example.org/a"
    folder = _ensure_source(cache, pin(url), fetch, url, "r1")
    assert (folder / "f.txt").read_text() == url
    _ensure_source(cache, pin(url), fetch, url, "r1")
    assert calls == [(url, "r1")]

    with pytest.raises(OSError):
        _ensure_source(cache, pin("https://example.org/b"), broken, "https://example.org/b", "r2")
    assert not list(cache.root.glob("checkouts/src__remote-*/r2"))

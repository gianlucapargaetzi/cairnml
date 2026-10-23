"""The dataset workflow end to end on local git and DVC storage: init, add, commit, push, get, cache."""

import json
import subprocess
from pathlib import Path

import pytest

from clearml.datasets import Dataset, DatasetCache, DatasetError, DatasetRepo, SampleWriter
from clearml.datasets.dvcutil import Dvc


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for name, value in (("NAME", "Tester"), ("EMAIL", "tester@example.com")):
        monkeypatch.setenv(f"GIT_AUTHOR_{name}", value)
        monkeypatch.setenv(f"GIT_COMMITTER_{name}", value)


@pytest.fixture
def origin(tmp_path):
    path = tmp_path / "server" / "acme" / "cars.git"
    subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True)
    return path


@pytest.fixture
def work(tmp_path, origin):
    path = tmp_path / "cars"
    subprocess.run(["git", "clone", "-q", str(origin), str(path)], check=True, capture_output=True)
    return path


@pytest.fixture
def storage(tmp_path):
    return tmp_path / "storage"


@pytest.fixture
def cache(tmp_path):
    return DatasetCache(tmp_path / "cache", max_bytes=10**9)


def write(path: Path, name: str, data: bytes) -> None:
    target = path / "data" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def native(work: Path) -> None:
    """Describe the files now in data/ as a native dataset: one sample per file, in the standard table."""
    data = work / "data"
    files = sorted(
        p.relative_to(data).as_posix() for p in data.rglob("*")
        if p.is_file() and p.name not in ("samples.parquet", "manifest.json")
    )
    writer = SampleWriter(data, {"file": "file"}, {}, source_root=data)
    for name in files:
        writer.add(name, "train", {"file": name}, {})
    writer.close()


def publish(repo: DatasetRepo, message: str) -> str:
    native(repo.path)
    repo.add()
    sha = repo.commit(message)
    repo.push()
    return sha


@pytest.fixture
def first(work, storage):
    repo = DatasetRepo(work)
    repo.init(remote_url=str(storage))
    write(work, "a.txt", b"alpha")
    write(work, "sub/b.txt", b"bravo")
    return repo, publish(repo, "first")


def test_init_needs_a_repository_with_an_origin(tmp_path):
    with pytest.raises(DatasetError, match="not a git repository"):
        DatasetRepo(tmp_path).init(remote_url=str(tmp_path / "s"))
    subprocess.run(["git", "init", "-q", str(tmp_path / "x")], check=True)
    with pytest.raises(DatasetError, match="no origin"):
        DatasetRepo(tmp_path / "x").init(remote_url=str(tmp_path / "s"))


def test_new_files_are_untracked_until_added(work, storage):
    repo = DatasetRepo(work)
    repo.init(remote_url=str(storage))
    write(work, "a.txt", b"alpha")
    status = repo.status()
    assert status["untracked"] == ["data/a.txt"]
    after = repo.add()
    assert after["added"]["added"] == ["data/a.txt"]
    assert after["untracked"] == []


def test_commit_without_add_is_refused(work, storage):
    repo = DatasetRepo(work)
    repo.init(remote_url=str(storage))
    write(work, "a.txt", b"alpha")
    native(work)
    repo.add()
    repo.commit("one")
    with pytest.raises(DatasetError, match="nothing to commit"):
        repo.commit("two")


def test_a_pushed_version_can_be_fetched_with_the_same_files(first, origin, cache):
    _repo, sha = first
    dataset = Dataset.get(str(origin), sha, cache=cache)
    assert dataset.commit == sha
    assert (dataset.path / "a.txt").read_bytes() == b"alpha"
    assert (dataset.path / "sub" / "b.txt").read_bytes() == b"bravo"
    assert dataset.content_hash.endswith(".dir")


def test_a_deleted_file_is_recorded_and_the_old_version_stays_intact(first, work, origin, cache):
    repo, old = first
    (work / "data" / "a.txt").unlink()
    write(work, "c.txt", b"charlie")
    assert repo.status()["not_added"]["deleted"] == ["data/a.txt"]
    new = publish(repo, "swap a for c")
    fresh = Dataset.get(str(origin), new, cache=cache)
    assert not (fresh.path / "a.txt").exists()
    assert (fresh.path / "c.txt").read_bytes() == b"charlie"
    previous = Dataset.get(str(origin), old, cache=cache)
    assert (previous.path / "a.txt").read_bytes() == b"alpha"
    assert previous.content_hash != fresh.content_hash


def test_a_commit_that_was_not_pushed_is_refused(first, work, origin, cache):
    repo, _sha = first
    write(work, "late.txt", b"late")
    native(work)
    repo.add()
    local = repo.commit("not pushed")
    with pytest.raises(DatasetError, match="is not on"):
        Dataset.get(str(origin), local, cache=cache)


def test_files_that_were_not_pushed_are_refused(first, work, origin, cache):
    repo, _sha = first
    write(work, "late.txt", b"late")
    native(work)
    repo.add()
    local = repo.commit("files not pushed")
    repo.git.push()  # the commit is on the remote, its files are not in storage
    with pytest.raises(DatasetError, match="not in storage"):
        Dataset.get(str(origin), local, cache=cache)


def test_a_cached_version_is_not_downloaded_again(first, origin, cache, monkeypatch):
    _repo, sha = first
    Dataset.get(str(origin), sha, cache=cache)

    def fail(*_args, **_kwargs):
        raise AssertionError("downloaded again")

    monkeypatch.setattr(Dvc, "pull", fail)
    assert (Dataset.get(str(origin), sha, cache=cache).path / "a.txt").exists()


def test_the_oldest_version_is_evicted_when_the_cache_is_over_its_limit(first, work, origin, tmp_path):
    repo, old = first
    write(work, "big.bin", b"x" * 200_000)
    new = publish(repo, "big")
    cache = DatasetCache(tmp_path / "small", max_bytes=10**9)
    first_version = Dataset.get(str(origin), old, cache=cache)
    Dataset.get(str(origin), new, cache=cache)
    cache.max_bytes = cache.usage() - 1
    assert cache.evict() >= 1
    assert not first_version.path.exists()
    assert (cache.checkout_dir(str(origin), new) / "data" / "big.bin").exists()


def test_a_version_in_use_is_not_evicted(first, origin, cache):
    _repo, sha = first
    dataset = Dataset.get(str(origin), sha, cache=cache)
    cache.max_bytes = 0
    with dataset.use(cache):
        assert cache.evict() == 0
        assert dataset.path.exists()
    assert cache.evict() == 1
    assert not dataset.path.exists()


# The footprint, check, push and register


def test_data_that_is_neither_native_nor_read_by_an_adapter_cannot_be_checked(work, storage):
    repo = DatasetRepo(work)
    repo.init(remote_url=str(storage))
    write(work, "a.txt", b"alpha")
    repo.add()
    with pytest.raises(DatasetError, match="samples.parquet"):
        repo.check()
    with pytest.raises(DatasetError, match="samples.parquet"):
        repo.commit("not native")


def test_check_wants_the_data_added_first(work, storage):
    repo = DatasetRepo(work)
    repo.init(remote_url=str(storage))
    write(work, "a.txt", b"alpha")
    native(work)
    with pytest.raises(DatasetError, match="not added"):
        repo.check()


def test_a_commit_carries_a_footprint_that_verifies_from_git_alone(first):
    from clearml.datasets import footprint

    repo, _sha = first
    assert footprint.verify(str(repo.path)) == []
    assert repo.problems() == []
    assert (repo.path / ".clearml" / "verify.py").is_file()
    assert (repo.path / ".github" / "workflows" / "dataset.yml").is_file()
    lock = json.loads((repo.path / "dataset.lock.json").read_text())
    assert lock["format"] == {"kind": "native"} and lock["samples"] == 2


def test_the_default_branch_is_published_through_a_branch_after_the_first_push(first, work):
    repo, _sha = first
    write(work, "c.txt", b"charlie")
    native(work)
    repo.add()
    repo.commit("add c")
    result = repo.push()
    assert result["branch"].startswith("incoming/") and result["commit"] == repo.git.head()
    assert result["branch"] in repo.git.remote_heads()


def test_push_refuses_a_lock_that_does_not_match_the_data(first, work):
    repo, _sha = first
    lock = work / "dataset.lock.json"
    body = json.loads(lock.read_text())
    body["samples"] = 99
    lock.write_text(json.dumps(body))
    subprocess.run(["git", "-C", str(work), "commit", "-qam", "edit the lock by hand"], check=True)
    with pytest.raises(DatasetError, match="checksum"):
        repo.push()


def test_push_refuses_an_uncommitted_lock(first, work):
    repo, _sha = first
    (work / "dataset.yaml").write_text("format: native\n# changed\n")
    with pytest.raises(DatasetError, match="uncommitted"):
        repo.push()


def test_a_dataset_with_an_adapter_is_checked_through_it(tmp_path, work, storage, cache):
    registry = tmp_path / "server" / "acme" / "registry.git"
    subprocess.run(["git", "init", "-q", "--bare", str(registry)], check=True)
    clone = tmp_path / "registry-work"
    subprocess.run(["git", "clone", "-q", str(registry), str(clone)], check=True, capture_output=True)
    (clone / "adapters").mkdir()
    (clone / "adapters" / "text_files.py").write_text(
        "from clearml.datasets import Adapter\n"
        "class TextFiles(Adapter):\n"
        "    modalities = {'file': 'file'}\n"
        "    labels = {}\n"
        "    def run(self, source, writer):\n"
        "        for p in sorted(source.glob('*.txt')):\n"
        "            writer.add(p.name, 'train', {'file': p.name}, {})\n"
    )
    subprocess.run(["git", "-C", str(clone), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(clone), "commit", "-qm", "adapter"], check=True)
    subprocess.run(["git", "-C", str(clone), "push", "-q", "origin", "HEAD"], check=True, capture_output=True)

    repo = DatasetRepo(work, cache=cache)
    repo.init(remote_url=str(storage), adapter="text_files", registry=str(registry))
    write(work, "a.txt", b"alpha")
    repo.add()
    lock = repo.check()
    assert lock["format"]["kind"] == "adapter" and lock["format"]["name"] == "text_files"
    assert lock["samples"] == 1 and len(lock["format"]["registry_commit"]) == 40
    repo.commit("with an adapter")
    assert repo.problems() == []


def test_an_adapter_that_finds_nothing_blocks_the_check(tmp_path, work, storage, cache):
    registry = tmp_path / "server" / "acme" / "registry.git"
    subprocess.run(["git", "init", "-q", "--bare", str(registry)], check=True)
    clone = tmp_path / "registry-work"
    subprocess.run(["git", "clone", "-q", str(registry), str(clone)], check=True, capture_output=True)
    (clone / "adapters").mkdir()
    (clone / "adapters" / "text_files.py").write_text(
        "from clearml.datasets import Adapter\n"
        "class TextFiles(Adapter):\n"
        "    modalities = {'file': 'file'}\n"
        "    labels = {}\n"
        "    def run(self, source, writer):\n"
        "        for p in sorted(source.glob('*.txt')):\n"
        "            writer.add(p.name, 'train', {'file': p.name}, {})\n"
    )
    subprocess.run(["git", "-C", str(clone), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(clone), "commit", "-qm", "adapter"], check=True)
    subprocess.run(["git", "-C", str(clone), "push", "-q", "origin", "HEAD"], check=True, capture_output=True)
    repo = DatasetRepo(work, cache=cache)
    repo.init(remote_url=str(storage), adapter="text_files", registry=str(registry))
    write(work, "picture.png", b"x")  # the adapter reads *.txt only
    repo.add()
    with pytest.raises(DatasetError, match="empty|no samples|adapter"):
        repo.check()


def test_register_needs_the_commit_to_be_on_the_default_branch(first, work, monkeypatch):
    from clearml.datasets import repo as repo_module

    repo, _sha = first
    calls = []
    monkeypatch.setattr(repo_module, "register_dataset", lambda origin, name=None, session=None: calls.append((origin, name)) or {"name": "cars"})
    write(work, "c.txt", b"charlie")
    native(work)
    repo.add()
    repo.commit("add c")
    repo.push()  # goes to incoming/<sha>, not to the default branch
    with pytest.raises(DatasetError, match="not on .* of the remote"):
        repo.register()
    assert calls == []
    default = repo.git.default_branch()
    repo.git.push_to(default)  # the pull request is merged
    assert repo.register(name="cars-data") == {"name": "cars"}
    assert calls and calls[0][1] == "cars-data"


def _registry_with_entry(tmp_path, entry: str):
    registry = tmp_path / "server" / "acme" / "registry.git"
    subprocess.run(["git", "init", "-q", "--bare", str(registry)], check=True)
    clone = tmp_path / "registry-work"
    subprocess.run(["git", "clone", "-q", str(registry), str(clone)], check=True, capture_output=True)
    (clone / "datasets").mkdir()
    (clone / "datasets" / "mine.yaml").write_text(entry)
    subprocess.run(["git", "-C", str(clone), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(clone), "commit", "-qm", "entry"], check=True)
    subprocess.run(["git", "-C", str(clone), "push", "-q", "origin", "HEAD"], check=True, capture_output=True)
    return registry


def test_a_registered_dataset_of_our_own_is_read_through_its_registry_entry(first, origin, tmp_path, cache):
    _repo, sha = first
    registry = _registry_with_entry(tmp_path, f"source: repo\nlink: {origin}\n")
    data = Dataset.get_external("mine", repo=str(registry), cache=cache)
    assert data.adapter == "native" and data.revision == sha
    assert sorted(s.id for s in data.samples()) == ["a.txt", "sub/b.txt"]
    assert next(data.samples()).modality("file").read_bytes() in (b"alpha", b"bravo")


def test_a_dataset_whose_footprint_does_not_hold_is_not_read(first, work, origin, tmp_path, cache):
    repo, _sha = first
    lock = work / "dataset.lock.json"
    body = json.loads(lock.read_text())
    body["samples"] = 99
    lock.write_text(json.dumps(body))
    subprocess.run(["git", "-C", str(work), "commit", "-qam", "edit the lock by hand"], check=True)
    repo.git.push()  # around the tool, onto the default branch
    registry = _registry_with_entry(tmp_path, f"source: repo\nlink: {origin}\n")
    with pytest.raises(DatasetError, match="checksum"):
        Dataset.get_external("mine", repo=str(registry), cache=cache)

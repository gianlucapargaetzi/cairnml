"""The dataset workflow end to end on local git and DVC storage: init, add, commit, push, get, cache."""

import subprocess
from pathlib import Path

import pytest

from clearml.datasets import Dataset, DatasetCache, DatasetError, DatasetRepo
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


def publish(repo: DatasetRepo, message: str) -> str:
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
    repo.add()
    local = repo.commit("not pushed")
    with pytest.raises(DatasetError, match="is not on"):
        Dataset.get(str(origin), local, cache=cache)


def test_files_that_were_not_pushed_are_refused(first, work, origin, cache):
    repo, _sha = first
    write(work, "late.txt", b"late")
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

"""Datasets stored in Git LFS (``storage: lfs``): init, add, push, get without DVC, the footprint, and the one-call create.

The LFS server is a local bare repository, which git-lfs serves from its own folder, so the whole path runs offline.
"""

import json
import subprocess
from pathlib import Path

import pytest

from cairnml.datasets import Dataset, DatasetCache, DatasetError, DatasetRepo, SampleWriter
from cairnml.datasets import footprint
from cairnml.datasets.dataset import fetch_commands

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "lfs", "version"], capture_output=True).returncode != 0, reason="git-lfs is not installed"
)


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
def cache(tmp_path):
    return DatasetCache(tmp_path / "cache", max_bytes=10**9)


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


def write(path: Path, name: str, data: bytes) -> None:
    target = path / "data" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def publish(repo: DatasetRepo, message: str) -> str:
    native(repo.path)
    repo.add()
    sha = repo.commit(message)
    repo.push()
    return sha


@pytest.fixture
def lfs_repo(work):
    repo = DatasetRepo(work)
    repo.init(storage="lfs")
    write(work, "a.bin", bytes(range(256)) * 40)  # 10 KB, larger than a pointer
    write(work, "sub/b.txt", b"bravo")
    return repo


def test_init_with_lfs_writes_no_dvc_and_records_the_storage(work):
    repo = DatasetRepo(work)
    where = repo.init(storage="lfs")
    assert "GitHub LFS" in where
    assert (work / footprint.CONFIG_FILE).read_text().splitlines()[1] == "storage: lfs"
    assert footprint.LFS_PATTERN in (work / footprint.ATTRIBUTES_FILE).read_text().splitlines()
    assert not (work / ".dvc").exists()
    assert repo.storage() == "lfs"


def test_the_storage_cannot_be_chosen_again_and_remote_does_not_apply(work, tmp_path):
    with pytest.raises(DatasetError, match="applies to s3 storage only"):
        DatasetRepo(work).init(remote_url=str(tmp_path / "s"), storage="lfs")
    DatasetRepo(work).init(storage="lfs")
    with pytest.raises(DatasetError, match="already set up"):
        DatasetRepo(work).init(storage="s3", remote_url=str(tmp_path / "s"))
    with pytest.raises(DatasetError, match="s3 or lfs"):
        DatasetRepo(work).init(storage="dvc")


def test_files_are_stored_as_lfs_pointers_and_pushed_to_the_lfs_of_the_remote(lfs_repo, work, origin, cache):
    sha = publish(lfs_repo, "first")
    assert (work / "data" / "a.bin").stat().st_size == 10240  # the working copy keeps the content
    tracked = subprocess.run(
        ["git", "-C", str(origin), "ls-tree", "-r", "--long", sha, "data"], capture_output=True, text=True, check=True
    ).stdout
    assert all(int(line.split()[3]) < footprint.POINTER_MAX_BYTES for line in tracked.splitlines())
    assert any((origin / "lfs" / "objects").rglob("*"))  # the content went to the LFS store of the remote

    dataset = Dataset.get(str(origin), sha, cache=cache)
    assert dataset.storage == "lfs"
    assert (dataset.path / "a.bin").read_bytes() == bytes(range(256)) * 40
    assert (dataset.path / "sub" / "b.txt").read_bytes() == b"bravo"
    assert dataset.content_hash == footprint.lfs_record(str(dataset.path.parent))["hash"]


def test_the_lock_records_the_lfs_objects_and_verify_passes(lfs_repo, work):
    publish(lfs_repo, "first")
    lock = json.loads((work / footprint.LOCK_FILE).read_text())
    assert "dvc" not in lock and lock["lfs"]["nfiles"] == 4  # two data files and the sample table and manifest
    assert footprint.verify(str(work)) == []
    assert lfs_repo.problems() == []


def test_a_changed_file_after_the_check_is_found(lfs_repo, work):
    publish(lfs_repo, "first")
    (work / "data" / "a.bin").write_bytes(b"changed")
    problems = footprint.verify(str(work))
    assert any("changed after the check" in p for p in problems)
    assert any("uncommitted" in p or "not committed" in p for p in lfs_repo.problems())


def test_a_dataset_with_raw_content_in_git_is_refused_at_add(work, monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")  # no git-lfs filter from the machine: only the repository's own is used
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / "config"))
    repo = DatasetRepo(work)
    repo.init(storage="lfs")
    write(work, "big.bin", b"x" * 5000)
    subprocess.run(["git", "-C", str(work), "config", "--unset", "filter.lfs.clean"], check=True)
    with pytest.raises(DatasetError, match="not set up"):
        repo.add()
    subprocess.run(["git", "-C", str(work), "config", "filter.lfs.clean", "cat"], check=True)  # the filter is set again
    (work / ".gitattributes").write_text("")  # but the pattern is missing, so git stores the content itself
    with pytest.raises(DatasetError, match="stored in git"):
        repo.add()


def test_status_groups_lfs_changes_like_the_dvc_status(lfs_repo, work):
    status = lfs_repo.status()
    assert status["untracked"] == ["data/a.bin", "data/sub/b.txt"]
    lfs_repo.add()
    assert lfs_repo.status()["added"]["added"] == ["data/a.bin", "data/sub/b.txt"]


def test_fetch_commands_for_an_lfs_dataset_are_git_and_lfs_only(origin):
    lines = fetch_commands(str(origin), "a" * 40, "lfs")
    assert lines[0].startswith("git clone ") and lines[-1].endswith("lfs pull")
    assert all("cairnml" not in line and "dvc" not in line for line in lines)
    assert fetch_commands(str(origin), "a" * 40, "s3") == [f"cairnml dataset get {origin} {'a' * 40}"]


def test_create_makes_the_remote_and_publishes_in_one_call(tmp_path, origin):
    prepared = tmp_path / "prepared"
    (prepared / "data").mkdir(parents=True)
    write(prepared, "one.bin", b"1" * 3000)
    write(prepared, "two.txt", b"two")
    native(prepared)
    source = prepared / "data"
    calls = []

    def fake_remote(name, private):
        calls.append((name, private))
        return str(origin)  # the bare repository stands in for the new GitHub repository

    result = DatasetRepo.create(
        tmp_path / "local", "cars", source, storage="lfs", create_remote=fake_remote
    )
    assert calls == [("cars", True)]
    assert result["storage"] == "lfs" and result["branch"] == "main"
    assert (tmp_path / "local" / "data" / "one.bin").read_bytes() == b"1" * 3000
    heads = subprocess.run(["git", "-C", str(origin), "branch", "--list"], capture_output=True, text=True).stdout
    assert "main" in heads
    assert footprint.verify(str(tmp_path / "local")) == []


def test_create_refuses_a_folder_that_is_not_empty_or_a_missing_source(tmp_path):
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "x").write_text("x")
    with pytest.raises(DatasetError, match="not a folder"):
        DatasetRepo.create(tmp_path / "new", "cars", tmp_path / "nothing", create_remote=lambda n, p: "unused")
    with pytest.raises(DatasetError, match="not empty"):
        DatasetRepo.create(tmp_path / "busy", "cars", tmp_path, create_remote=lambda n, p: "unused")


def test_a_checkout_without_lfs_pull_verifies_to_the_same_record(lfs_repo, work, origin, tmp_path, monkeypatch):
    """The verify workflow checks out the repository without its LFS content: the pointers give the same footprint."""
    publish(lfs_repo, "first")
    monkeypatch.setenv("GIT_LFS_SKIP_SMUDGE", "1")
    clone = tmp_path / "ci"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    assert (clone / "data" / "a.bin").read_bytes().startswith(footprint.LFS_POINTER)
    assert footprint.verify(str(clone)) == []
    assert footprint.lfs_record(str(clone)) == footprint.lfs_record(str(work))

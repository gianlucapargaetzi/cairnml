"""The dataset workflow end to end on local git and DVC storage: init, add, commit, push, get, cache."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cairnml.cli.dataset import __main__ as dataset_cli
from cairnml.datasets import Dataset, DatasetCache, DatasetError, DatasetRepo, SampleWriter, footprint
from cairnml.datasets.dvcutil import Dvc
from cairnml.datasets.repo import protection_problems


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
    from cairnml.datasets import footprint

    repo, _sha = first
    assert footprint.verify(str(repo.path)) == []
    assert repo.problems() == []
    assert (repo.path / ".cairnml" / "verify.py").is_file()
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
        "from cairnml.datasets import Adapter\n"
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
        "from cairnml.datasets import Adapter\n"
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
    from cairnml.datasets import repo as repo_module

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


def checks_by_name(repo):
    return {item['check']: item for item in repo.doctor()}


def test_doctor_lists_what_is_missing_before_the_set_up(work):
    checks = checks_by_name(DatasetRepo(work))
    assert checks['origin']['ok'] is True
    assert checks['set-up']['ok'] is False and 'init' in checks['set-up']['message']
    assert checks['verify script']['ok'] is False


def test_doctor_says_when_the_repository_has_no_origin(tmp_path):
    local = tmp_path / 'local'
    subprocess.run(['git', 'init', '-q', str(local)], check=True)
    checks = checks_by_name(DatasetRepo(local))
    assert checks['origin']['ok'] is False


def test_doctor_finds_a_set_up_repository_and_names_a_missing_gh(first, work, monkeypatch):
    monkeypatch.setattr('cairnml.datasets.repo.shutil.which', lambda name: None)
    checks = checks_by_name(DatasetRepo(work))
    assert checks['set-up']['ok'] is True
    assert checks['workflow']['ok'] is True
    assert checks['verify script']['ok'] is True
    assert checks['GitHub command line']['ok'] is False
    assert 'Install it from' in checks['GitHub command line']['message']


def test_protection_problems_name_each_missing_setting():
    right = {
        'required_pull_request_reviews': {},
        'allow_force_pushes': {'enabled': False},
        'required_status_checks': {'contexts': ['verify']},
    }
    assert protection_problems(right) == []
    assert protection_problems({}) == [
        'pull requests are not required before merging',
    ]
    forced = dict(right, allow_force_pushes={'enabled': True}, required_status_checks={'checks': [{'context': 'lint'}]})
    assert protection_problems(forced) == ['force pushes are allowed']


def test_check_writes_a_manifest_with_modalities_labels_and_splits(first):
    from cairnml.datasets import footprint

    repo, _sha = first
    manifest = json.loads((repo.path / "manifest.json").read_text())
    assert manifest["schema_version"] == 1
    assert manifest["samples"] == 2
    assert manifest["splits"] == {"train": 2}
    assert manifest["modalities"] == {"file": "file"} and manifest["labels"] == {}
    assert footprint.verify(str(repo.path)) == []


# The local adapter: adapter.py at the top of the repository, no registry


CARS_CSV = b"id,split,file,class\n1,train,a.txt,sedan\n2,val,sub/b.txt,truck\n"

CSV_ADAPTER = (
    "import csv\n"
    "import os\n"
    "from cairnml.datasets import Adapter\n"
    "if os.environ.get('CAIRNML_TEST_MARK'):\n"  # proves whether the file was run (see the registry test)
    "    open(os.environ['CAIRNML_TEST_MARK'], 'w').close()\n"
    "class CarsCsv(Adapter):\n"
    "    modalities = {'file': 'file'}\n"
    "    labels = {'class': 'class'}\n"
    "    def run(self, source, writer):\n"
    "        with open(source / 'cars.csv', newline='') as handle:\n"
    "            for row in csv.DictReader(handle):\n"
    "                writer.add(row['id'], row['split'], {'file': row['file']}, {'class': row['class']})\n"
)


def local_dataset(work: Path, storage: Path, cache, adapter_source: str = CSV_ADAPTER) -> DatasetRepo:
    repo = DatasetRepo(work, cache=cache)
    repo.init(remote_url=str(storage), adapter="local")
    write(work, "cars.csv", CARS_CSV)
    write(work, "a.txt", b"alpha")
    write(work, "sub/b.txt", b"bravo")
    (work / "adapter.py").write_text(adapter_source)
    return repo


def test_a_local_adapter_reads_a_csv_and_the_check_passes(work, storage, cache):
    repo = local_dataset(work, storage, cache)
    assert "adapter: local" in (work / "dataset.yaml").read_text()
    assert "registry" not in (work / "dataset.yaml").read_text()
    repo.add()
    lock = repo.check()
    digest = footprint.sha256_bytes((work / "adapter.py").read_bytes())
    assert lock["format"] == {"kind": "local", "name": "local", "adapter_sha256": digest}
    assert lock["samples"] == 2 and lock["labels"] == {"class": "class"}
    assert not [name for name in sys.modules if name.startswith("cairnml_local_adapter_")]  # nothing kept
    repo.commit("with a local adapter")
    assert repo.problems() == []
    tracked = subprocess.run(["git", "-C", str(work), "ls-files", "adapter.py"], capture_output=True, text=True)
    assert tracked.stdout.strip() == "adapter.py"


@pytest.mark.parametrize(
    "source, message",
    [
        ("def run(:\n", "local adapter 'adapter.py' cannot be loaded"),
        ("from cairnml.datasets import Adapter\nx = 1\n", "adapter.py must define exactly one Adapter class, found 0"),
        (
            CSV_ADAPTER.replace("row['class']", "row['colour']"),
            "local adapter 'adapter.py' could not read data/: KeyError",
        ),
        (
            CSV_ADAPTER.replace("{'file': row['file']}", "{'picture': row['file']}"),
            "local adapter 'adapter.py': .*modalities",
        ),
    ],
)
def test_a_broken_local_adapter_error_names_it(work, storage, cache, source, message):
    repo = local_dataset(work, storage, cache, adapter_source=source)
    repo.add()
    with pytest.raises(DatasetError, match=message):
        repo.check()


def test_a_local_adapter_writes_tabular_labels_without_json_text(work, storage, cache):
    adapter = (
        "from cairnml.datasets import Adapter\n"
        "import csv\n"
        "class Greenhouse(Adapter):\n"
        "    modalities = {'table': 'table'}\n"
        "    labels = {'features': 'tabular', 'energy': 'number'}\n"
        "    def run(self, source, writer):\n"
        "        for row in csv.DictReader(open(source / 'cars.csv', newline='')):\n"
        "            features = {'temp': float(row['id']), 'plant': row['class']}\n"
        "            writer.add(row['id'], row['split'], {'table': row['file']}, {'features': features, 'energy': 12.5})\n"
    )
    repo = local_dataset(work, storage, cache, adapter_source=adapter)
    repo.add()
    lock = repo.check()
    assert lock["labels"] == {"features": "tabular", "energy": "number"}
    manifest = json.loads((repo.path / "manifest.json").read_text())
    assert manifest["labels"] == {"features": "tabular", "energy": "number"}


def test_a_local_adapter_that_is_missing_is_named(work, storage, cache):
    repo = local_dataset(work, storage, cache)
    (work / "adapter.py").unlink()
    repo.add()
    with pytest.raises(DatasetError, match="local adapter 'adapter.py' is missing"):
        repo.check()


def test_an_edited_local_adapter_fails_the_footprint_check(work, storage, cache):
    repo = local_dataset(work, storage, cache)
    repo.add()
    repo.commit("first")
    (work / "adapter.py").write_text(CSV_ADAPTER + "# a later change\n")
    subprocess.run(["git", "-C", str(work), "commit", "-qam", "change the adapter by hand"], check=True)
    problems = repo.problems()
    assert any("was checked with another adapter.py" in p and "commit" in p for p in problems), problems
    with pytest.raises(DatasetError, match="another adapter.py"):
        repo.push()


def test_a_registry_entry_does_not_run_a_local_adapter(first_local_work, origin, tmp_path, cache, monkeypatch):
    registry = _registry_with_entry(tmp_path, f"source: repo\nlink: {origin}\n")
    mark = tmp_path / "ran"
    monkeypatch.setenv("CAIRNML_TEST_MARK", str(mark))
    with pytest.raises(DatasetError, match="is read by its own adapter.py .*does not run"):
        Dataset.get_external("mine", repo=str(registry), cache=cache)
    assert not mark.exists()


def test_reading_a_local_dataset_with_get_does_not_run_its_adapter(first_local_work, origin, tmp_path, cache, monkeypatch):
    _repo, sha = first_local_work
    mark = tmp_path / "ran"
    monkeypatch.setenv("CAIRNML_TEST_MARK", str(mark))
    dataset = Dataset.get(str(origin), sha, cache=cache, record=False)
    assert (dataset.path / "a.txt").read_bytes() == b"alpha"
    assert not mark.exists()


@pytest.fixture
def first_local_work(work, storage, cache):
    repo = local_dataset(work, storage, cache)
    return repo, publish(repo, "first")


def test_init_with_the_local_adapter_writes_the_line_and_needs_no_registry(work, storage):
    code = dataset_cli.cli(["init", "--path", str(work), "--remote", str(storage), "--adapter", "local"])
    assert code == 0
    config = (work / "dataset.yaml").read_text()
    assert "adapter: local\n" in config and "registry" not in config


def test_init_with_a_registry_and_the_local_adapter_is_refused(work, storage):
    code = dataset_cli.cli(
        ["init", "--path", str(work), "--remote", str(storage), "--adapter", "local", "--registry", "https://x/y.git"]
    )
    assert code == 1
    assert not (work / "dataset.yaml").exists()


def test_init_with_a_registry_adapter_keeps_its_line_and_writes_no_registry(work, storage):
    code = dataset_cli.cli(["init", "--path", str(work), "--remote", str(storage), "--adapter", "coco"])
    assert code == 0
    config = (work / "dataset.yaml").read_text()
    assert "adapter: coco\n" in config and "registry" not in config

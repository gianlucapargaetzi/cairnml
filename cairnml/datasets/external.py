"""Outside datasets (Hugging Face, git, local folders) read through adapters.

A run names a dataset by its pin file name and a commit of the ``external-datasets`` repository. That commit
fixes the pin file (where the data comes from, which adapter reads it) and the adapter code. The revision
of the source itself is taken from the run if it asks for one, else from the pin file, else the latest;
the revision that was used is always recorded.
"""

import importlib.util
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ._shared import git_token, mirror, record, resolve_commit
from .adapter import Adapter, SampleWriter
from .cache import DatasetCache
from .errors import DatasetError
from .schema import SAMPLES_FILE, SchemaError, label_column, modality_column, read_manifest
from .sources import parse_fetch, source_for


@dataclass
class Pin:
    name: str
    source: str
    link: str
    adapter: Optional[str] = None
    revision: Optional[str] = None
    overrides: List[Dict[str, Any]] = field(default_factory=list)
    fetch: Optional[List[Dict[str, Any]]] = None  # web: the files to fetch, with checksums and unpacking

    def adapter_for(self, revision: str) -> str:
        """An override whose revisions include this one (by prefix) wins; otherwise the default adapter."""
        if self.adapter is None:
            return None
        for override in self.overrides:
            if any(revision.startswith(str(prefix)) for prefix in override.get("revisions", [])):
                return override["adapter"]
        return self.adapter


def read_pin(tree: Path, name: str) -> Pin:
    import yaml

    path = tree / "datasets" / f"{name}.yaml"
    if not path.exists():
        known = sorted(p.stem for p in (tree / "datasets").glob("*.yaml"))
        raise DatasetError(f"no dataset {name!r} in the external-datasets repository; known: {', '.join(known) or 'none'}")
    data = yaml.safe_load(path.read_text()) or {}
    # A web dataset may describe its files in ``fetch`` instead of ``link``: the addresses, checksums and unpacking.
    fetch = None
    if data.get("fetch") is not None:
        if data.get("source") != "web":
            raise DatasetError(f"{path.name}: fetch is only for a source of web")
        if data.get("link"):
            raise DatasetError(f"{path.name} sets both link and fetch: keep one of them")
        fetch = parse_fetch(data["fetch"], path.name)
    # A dataset of our own (source: repo) says in its own repository how it is read; the others need an adapter here.
    keys = ("source",) if fetch is not None else ("source", "link")
    for key in keys + (() if data.get("source") == "repo" else ("adapter",)):
        if not data.get(key):
            raise DatasetError(f"{path.name} must set {key!r}")
    return Pin(
        name=name,
        source=data["source"],
        link=data.get("link") or [entry["url"] for entry in fetch],
        adapter=data.get("adapter"),
        revision=data.get("revision"),
        overrides=data.get("overrides") or [],
        fetch=fetch,
    )


def load_adapter(tree: Path, adapter_name: str) -> Adapter:
    path = tree / "adapters" / f"{adapter_name}.py"
    if not path.exists():
        raise DatasetError(f"adapter {adapter_name!r} is not in the external-datasets repository (adapters/{path.name})")
    spec = importlib.util.spec_from_file_location(f"cairnml_adapter_{adapter_name}", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise DatasetError(f"adapter {adapter_name!r} cannot be loaded: {exc}") from exc
    found = [
        obj for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, Adapter) and obj is not Adapter and obj.__module__ == module.__name__
    ]
    if len(found) != 1:
        raise DatasetError(f"adapters/{path.name} must define exactly one Adapter class, found {len(found)}")
    return found[0]()


class Sample:
    def __init__(self, row: Dict[str, Any], root: Path) -> None:
        self._row = row
        self._root = root

    @property
    def id(self) -> str:
        return self._row["sample_id"]

    @property
    def split(self) -> str:
        return self._row["split"]

    def modality(self, name: str):
        """The file of one modality: a path, or the URI text when the sample points outside the source."""
        value = self._row[modality_column(name)]
        return value if "://" in value else self._root / value

    def label(self, name: str):
        return self._row[label_column(name)]


class ExternalDataset:
    """An outside dataset in the canonical format: samples with named modalities and labels."""

    def __init__(self, info: Dict[str, str], output: Path, root: Path) -> None:
        self.info = info
        self.path = output
        self.root = root
        manifest = read_manifest(output)
        self.modalities: Dict[str, str] = manifest["modalities"]
        self.labels: Dict[str, str] = manifest["labels"]
        self._table = None

    def __getattr__(self, name: str):
        try:
            return self.__dict__["info"][name]
        except KeyError:
            raise AttributeError(name) from None

    @property
    def table(self):
        if self._table is None:
            import pyarrow.parquet as pq

            self._table = pq.read_table(self.path / SAMPLES_FILE)
        return self._table

    def __len__(self) -> int:
        return self.table.num_rows

    def samples(self, split: Optional[str] = None) -> Iterator[Sample]:
        for row in self.table.to_pylist():
            if split is None or row["split"] == split:
                yield Sample(row, self.root)


def _default_repo() -> str:
    from ..config import config

    repo = config.get("sdk.datasets.external_repo", None) or config.get("datasets.external_repo", None)
    if not repo:
        raise DatasetError("name the external-datasets repository: repo=..., or sdk.datasets.external_repo in the configuration")
    return repo


def registry_tree(cache: DatasetCache, repo: str, commit: Optional[str] = None) -> Tuple[Path, str]:
    """The files of the registry repository at a commit (the latest of its default branch if None), and the commit."""
    git = mirror(cache, repo, git_token(repo))
    if commit is None:
        git.fetch_all()
        sha = git.resolve("HEAD")
    else:
        sha = resolve_commit(git, repo, commit)
    tree = cache.checkout_dir(repo, sha)
    if not cache.is_ready(tree):
        with cache.downloading():
            if tree.exists():
                shutil.rmtree(tree)
            git.archive_to(sha, tree)
            cache.mark_ready(tree)
    return tree, sha


def get_external(
    name: str,
    commit: Optional[str] = None,
    revision: Optional[str] = None,
    repo: Optional[str] = None,
    alias: Optional[str] = None,
    cache: Optional[DatasetCache] = None,
) -> ExternalDataset:
    """The dataset ``name`` of the external-datasets repository at ``commit``, read by its adapter.

    :param name: the pin file name (``datasets/<name>.yaml``)
    :param commit: commit of the external-datasets repository. It fixes the pin file and the adapter code. If None,
        the latest commit of the repository's default branch is used, and the run records which one that was.
        Pass a commit to rerun exactly what an earlier run used.
    :param revision: revision of the source to use (for Hugging Face a commit sha). If None, the pin file's
        revision, else the latest. The revision used is recorded on the run.
    :param repo: URL of the external-datasets repository (default: ``sdk.datasets.external_repo``)
    :param alias: the name the run records the dataset under (default: ``name``)
    """
    cache = cache or DatasetCache.default()
    repo = repo or _default_repo()
    tree, sha = registry_tree(cache, repo, commit)

    pin = read_pin(tree, name)
    if pin.source == "repo":
        return _get_own_dataset(cache, tree, sha, repo, pin, revision, alias)
    source = source_for(pin.source)
    link = pin.fetch if pin.fetch is not None else pin.link
    if pin.source == "local" and not Path(link.replace("file://", "", 1)).expanduser().is_absolute():
        link = str(tree / link)  # a folder inside the external-datasets repository itself
    resolved = source.resolve(link, revision or pin.revision)
    adapter_name = pin.adapter_for(resolved)

    source_root = _ensure_source(cache, source, link, resolved)
    output = cache.root / "checkouts" / f"ext__{name}" / f"{sha[:12]}-{resolved[:12]}"
    if not cache.is_ready(output):
        _build(cache, tree, adapter_name, source_root, output, pin, resolved, sha, repo)
    cache.touch(output)
    if source.downloads:  # a local folder is the user's own: leave no marker in it
        cache.touch(source_root)

    info = {
        "name": name,
        "external_repo": repo,
        "repo_commit": sha,
        "source": pin.source,
        "link": pin.link,
        "revision": resolved,
        "adapter": adapter_name,
    }
    cache.evict()
    record(alias or name, {"kind": "external", **info})
    return ExternalDataset(info, output, source_root)


def _get_own_dataset(cache, tree, sha, repo, pin, revision, alias) -> ExternalDataset:
    """A dataset of our own: its repository says how it is read, and its footprint says it was checked."""
    from . import footprint
    from .dataset import Dataset

    git = mirror(cache, pin.link, git_token(pin.link))
    wanted = revision or pin.revision
    if wanted is None:
        git.fetch_all()
        resolved = git.resolve("HEAD")
    else:
        resolved = resolve_commit(git, pin.link, wanted)
    dataset = Dataset.get(pin.link, resolved, cache=cache, record=False)
    checkout = dataset.path.parent
    problems = footprint.verify(str(checkout))
    if problems:
        raise DatasetError(
            f"{pin.link}@{resolved[:10]} does not pass its footprint check:\n  - " + "\n  - ".join(problems)
        )
    lock = json.loads((checkout / footprint.LOCK_FILE).read_text())
    fmt = lock["format"]
    adapter_name = fmt.get("name") if fmt["kind"] == "adapter" else None
    if adapter_name is None:
        output = dataset.path  # native: the standard sample table is in data/ itself
    else:
        output = cache.root / "checkouts" / f"own__{pin.name}" / f"{sha[:12]}-{resolved[:12]}"
        if not cache.is_ready(output):
            _build(cache, tree, adapter_name, dataset.path, output, pin, resolved, sha, repo)
        cache.touch(output)
    info = {
        "name": pin.name,
        "external_repo": repo,
        "repo_commit": sha,
        "source": "repo",
        "link": pin.link,
        "revision": resolved,
        "adapter": adapter_name or "native",
    }
    record(alias or pin.name, {"kind": "own", **info, "content_hash": lock["dvc"]["hash"]})
    return ExternalDataset(info, output, dataset.path)


def _ensure_source(cache: DatasetCache, source, link: str, revision: str) -> Path:
    if not source.downloads:
        return source.local_root(link)
    import hashlib

    folder = cache.root / "checkouts" / f"src__{source.kind}-{hashlib.sha1(str(link).encode()).hexdigest()[:10]}" / revision
    if not cache.is_ready(folder):
        with cache.downloading():
            if folder.exists():
                shutil.rmtree(folder)
            try:
                source.fetch(link, revision, folder)
            except Exception:
                shutil.rmtree(folder, ignore_errors=True)
                raise
            cache.mark_ready(folder)
    return folder


def _build(cache, tree, adapter_name, source_root, output, pin, resolved, repo_commit, repo) -> None:
    adapter = load_adapter(tree, adapter_name)
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    try:
        writer = SampleWriter(output, dict(adapter.modalities), dict(adapter.labels), source_root=source_root)
        try:
            adapter.run(source_root, writer)
            count = writer.close(
                {
                    "dataset": pin.name,
                    "source": pin.source,
                    "link": pin.link,
                    "revision": resolved,
                    "adapter": adapter_name,
                    "external_repo": repo,
                    "repo_commit": repo_commit,
                }
            )
        except SchemaError as exc:
            raise DatasetError(f"adapter {adapter_name!r} for {pin.link}@{resolved[:10]}: {exc}") from None
        except DatasetError:
            raise
        except Exception as exc:
            raise DatasetError(
                f"adapter {adapter_name!r} could not read {pin.link}@{resolved[:10]}: {type(exc).__name__}: {exc}. "
                f"The source may have changed its layout: add an adapter for this revision to the pin file's overrides."
            ) from exc
        if count == 0:
            raise DatasetError(f"adapter {adapter_name!r} found no samples in {pin.link}@{resolved[:10]}")
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        try:
            output.parent.rmdir()  # the dataset's folder, when this was its only version
        except OSError:
            pass
        raise
    cache.mark_ready(output)

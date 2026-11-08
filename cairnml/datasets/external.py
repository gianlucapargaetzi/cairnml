"""Outside datasets read through adapters.

A run names a dataset by its pin file name and a commit of the ``external-datasets`` repository. That commit
fixes the pin file (where the data comes from, which adapter reads it) and the adapter code. The adapter says how
its source is fetched (``fetch``, and ``resolve`` for the revision) and which Python packages it needs
(``REQUIREMENTS``). The revision of the source itself is taken from the run if it asks for one, else from the pin
file, else the latest; the revision that was used is always recorded.

A ``local`` source is a folder that is used where it is; every other source is fetched by its adapter.
"""

import hashlib
import importlib.metadata
import json
import re
import shutil
import types
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ._shared import git_token, mirror, record, resolve_commit
from .adapter import Adapter, SampleWriter
from .cache import DatasetCache
from .errors import DatasetError
from .schema import SAMPLES_FILE, SchemaError, from_arrow_value, label_column, modality_column, read_manifest
from .sources import LocalFolder

LOCAL = "local"  # the source of a folder used where it is; every other source is fetched by the adapter


@dataclass
class Pin:
    name: str
    source: str
    link: Any  # what the adapter's fetch receives: an address, or the list of files a web adapter reads
    adapter: Optional[str] = None
    revision: Optional[str] = None
    overrides: List[Dict[str, Any]] = field(default_factory=list)

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
    if data.get("source") == "repo":
        link = data.get("link") or "<repository>"
        raise DatasetError(
            f"{path.name} names {link} as a dataset of its own (source: repo). Own datasets are not in the registry: "
            f'read them with Dataset.get("{link}", "<commit>")'
        )
    # What the adapter's fetch receives: ``link``, or ``fetch`` for a list of files (its shape is the adapter's to read).
    if data.get("link") is not None and data.get("fetch") is not None:
        raise DatasetError(f"{path.name} sets both link and fetch: keep one of them")
    link = data.get("link") if data.get("link") is not None else data.get("fetch")
    for key in ("source", "adapter"):
        if not data.get(key):
            raise DatasetError(f"{path.name} must set {key!r}")
    if not link:
        raise DatasetError(f"{path.name} must set 'link' (or 'fetch')")
    return Pin(
        name=name,
        source=data["source"],
        link=link,
        adapter=data.get("adapter"),
        revision=data.get("revision"),
        overrides=data.get("overrides") or [],
    )


def load_adapter_module(tree: Path, adapter_name: str) -> Tuple[Adapter, types.ModuleType]:
    """The adapter of the registry and its module (which holds ``REQUIREMENTS``, ``fetch`` and ``resolve`` if it has them)."""
    path = tree / "adapters" / f"{adapter_name}.py"
    if not path.exists():
        raise DatasetError(f"adapter {adapter_name!r} is not in the external-datasets repository (adapters/{path.name})")
    return _instantiate(path, f"cairnml_adapter_{adapter_name}", f"adapter {adapter_name!r}", f"adapters/{path.name}")


def load_adapter(tree: Path, adapter_name: str) -> Adapter:
    return load_adapter_module(tree, adapter_name)[0]


def load_local_adapter(path: Path) -> Adapter:
    """The adapter of a dataset that says ``adapter: local``: the class in ``adapter.py`` at the top of its repository.

    The file is loaded under a unique module name that is not put in ``sys.modules``, and the repository is not put on
    ``sys.path``, so nothing leaks from one dataset to the next. Loading runs the file's code: only call this for a
    repository the user trusts (see the footprint module for who does).
    """
    if not path.is_file():
        raise DatasetError(
            f"local adapter '{path.name}' is missing: dataset.yaml says adapter: local, so put {path.name} at the top of "
            f"the dataset repository"
        )
    return _instantiate(path, f"cairnml_local_adapter_{uuid.uuid4().hex}", f"local adapter '{path.name}'", path.name)[0]


def _instantiate(path: Path, module_name: str, label: str, where: str) -> Tuple[Adapter, types.ModuleType]:
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    try:
        # Compiled from the source in memory: the import would write __pycache__ next to the file, into the repository.
        code = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
        exec(code, module.__dict__)
    except Exception as exc:
        raise DatasetError(f"{label} cannot be loaded: {exc}") from exc
    found = [
        obj for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, Adapter) and obj is not Adapter and obj.__module__ == module.__name__
    ]
    if len(found) != 1:
        raise DatasetError(f"{where} must define exactly one Adapter class, found {len(found)}")
    return found[0](), module


def adapter_requirements(module: types.ModuleType, adapter_name: str) -> List[str]:
    """The exact pip requirements of an adapter (``REQUIREMENTS``), checked to be a list of ``name==version`` strings."""
    requirements = getattr(module, "REQUIREMENTS", None) or []
    if not isinstance(requirements, (list, tuple)) or not all(isinstance(r, str) for r in requirements):
        raise DatasetError(f"adapter {adapter_name!r}: REQUIREMENTS must be a list of requirement strings")
    for requirement in requirements:
        _exact_pin(requirement, adapter_name)
    return list(requirements)


_EXACT = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([A-Za-z0-9][A-Za-z0-9.!+_-]*)\s*")


def _exact_pin(requirement: str, adapter_name: str) -> Tuple[str, str]:
    match = _EXACT.fullmatch(requirement)
    if not match:
        raise DatasetError(
            f"adapter {adapter_name!r}: REQUIREMENTS entry {requirement!r} must be an exact pin, name==version"
        )
    return match.group(1), match.group(2)


def check_requirements(requirements: List[str], adapter_name: str) -> None:
    """Refuse to run an adapter whose exact requirements are not installed. Nothing is installed here."""
    for requirement in requirements:
        name, version = _exact_pin(requirement, adapter_name)
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed = None
        if installed != version:
            found = "is not installed" if installed is None else f"is {installed}"
            raise DatasetError(
                f'adapter {adapter_name!r} needs {name} {version}, but {name} {found}. '
                f'Install it with: pip install "{requirement}"'
            )


def requirements_digest(requirements: List[str]) -> str:
    return hashlib.sha256(json.dumps(list(requirements)).encode()).hexdigest()[:12]


class Sample:
    def __init__(self, row: Dict[str, Any], root: Path, labels: Optional[Dict[str, str]] = None) -> None:
        self._row = row
        self._root = root
        self._labels = labels or {}

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
        """The label: a dict of columns for a tabular label, a number for number, else as the adapter wrote it."""
        return from_arrow_value(self._labels.get(name, ""), self._row[label_column(name)])


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
                yield Sample(row, self.root, self.labels)


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
    link = pin.link
    if pin.source == LOCAL:
        if not Path(str(link).replace("file://", "", 1)).expanduser().is_absolute():
            link = str(tree / link)  # a folder inside the external-datasets repository itself
        resolved = LocalFolder().resolve(link, revision or pin.revision)
    else:
        resolved = _resolve_remote(tree, pin, link, revision or pin.revision)
    adapter_name = pin.adapter_for(resolved)
    adapter, module = load_adapter_module(tree, adapter_name)
    requirements = adapter_requirements(module, adapter_name)

    if pin.source == LOCAL:
        source_root = LocalFolder().local_root(link)
    else:
        fetch = getattr(module, "fetch", None)
        if fetch is None:
            raise DatasetError(f"adapter {adapter_name!r} has no fetch for source {pin.source!r}")
        check_requirements(requirements, adapter_name)  # before the download, which may need them
        source_root = _ensure_source(cache, pin, fetch, link, resolved)

    output = cache.root / "checkouts" / f"ext__{name}" / f"{sha[:12]}-{resolved[:12]}-{requirements_digest(requirements)}"
    if not cache.is_ready(output):
        _build(cache, adapter, adapter_name, requirements, source_root, output, pin, resolved, sha, repo)
    cache.touch(output)
    if pin.source != LOCAL:  # a local folder is the user's own: leave no marker in it
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


def _resolve_remote(tree: Path, pin: Pin, link: Any, revision: Optional[str]) -> str:
    """The revision of a source that is fetched: the one given, else what the pin's adapter resolves it to."""
    if revision:
        return revision
    _, module = load_adapter_module(tree, pin.adapter)
    resolve = getattr(module, "resolve", None)
    if resolve is None:
        raise DatasetError(
            f"adapter {pin.adapter!r} has no resolve for source {pin.source!r}: give the revision in the pin file or the run"
        )
    return resolve(link, None)


def _ensure_source(cache: DatasetCache, pin: Pin, fetch, link: Any, revision: str) -> Path:
    label = re.sub(r"\W", "_", pin.source)
    folder = cache.root / "checkouts" / f"src__{label}-{hashlib.sha1(str(link).encode()).hexdigest()[:10]}" / revision
    if not cache.is_ready(folder):
        with cache.downloading():
            if folder.exists():
                shutil.rmtree(folder)
            try:
                fetch(link, revision, folder)
            except Exception:
                shutil.rmtree(folder, ignore_errors=True)
                raise
            cache.mark_ready(folder)
    return folder


def _build(cache, adapter, adapter_name, requirements, source_root, output, pin, resolved, repo_commit, repo) -> None:
    check_requirements(requirements, adapter_name)
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

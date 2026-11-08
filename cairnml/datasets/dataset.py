"""Using a dataset in a training script: ``Dataset.get(repo, commit)``.

The dataset is fetched into the user's cache once and reused by every later run on the machine. The run
records which repository and commit it used, and the content hash of the files.
"""

import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from . import footprint
from ._shared import default_alias, git_token, mirror, record, resolve_commit
from .cache import DatasetCache
from .credentials import fetch_storage_keys, resolve_dataset
from .dvcutil import Dvc
from .errors import DatasetError
from .gitutil import Git
from .repo import DATA_DIR, DVC_FILE, github_name

if TYPE_CHECKING:
    from .external import ExternalDataset


def fetch_commands(repo: str, commit: str, storage: str = "s3") -> List[str]:
    """How to fetch one version of a dataset by hand, as shell commands (the web app shows them).

    A dataset in LFS needs only git and git-lfs: clone the repository, check out the commit and pull the files. A
    dataset in S3 is fetched by the tool, which holds the keys for the bucket.
    """
    if storage == "lfs":
        folder = default_alias(repo)
        return [f"git clone {repo} {folder}", f"git -C {folder} checkout {commit}", f"git -C {folder} lfs pull"]
    return [f"cairnml dataset get {repo} {commit}"]


def _storage_of(checkout: Path) -> str:
    config = checkout / footprint.CONFIG_FILE
    if not config.is_file():
        return "s3"
    try:
        return footprint.storage_kind(footprint.parse_config(config.read_text()))
    except ValueError as exc:
        raise DatasetError(f"{checkout}: {exc}") from None


def _is_address(reference: str) -> bool:
    # an address starts with its host: "github.com/owner/name", not a name that merely contains "github.com"
    return "://" in reference or reference.startswith("git@") or reference.lower().startswith(("github.com/", "www.github.com/"))


def _resolve_reference(reference: str):
    """(repository address, dataset id, reference name) of what a reference names. A local folder is read as it is (no
    server); a repository address is resolved by the server, and read by its address when the server cannot be asked."""
    if Path(reference).exists():
        return reference, None, None
    task = os.environ.get("CAIRNML_TASK_ID") or None
    try:
        found = resolve_dataset(reference, task=task)
    except DatasetError:
        found = {}
    if not found:
        # the server does not keep it (or cannot be asked): a repository address is read by its address
        if _is_address(reference):
            return reference, None, None
        raise DatasetError(f"no dataset {reference} that you can see")
    kind, source = found.get("kind"), found.get("source")
    if kind == "repo":
        repo = reference if _is_address(reference) else f"https://github.com/{found['ref']}"
    elif kind == "registry_entry" and source == "repo" and (found.get("link") or "").startswith("https://github.com/"):
        repo = found["link"]
    else:
        raise DatasetError(f"{reference} is not a repository dataset: read it with get_external")
    return repo, found.get("id"), found.get("reference") or None


class Dataset:
    """One version of a local dataset, available as a folder of files."""

    def __init__(
        self, repo: str, commit: str, path: Path, content_hash: Optional[str], storage: str = "s3"
    ) -> None:
        self.repo = repo
        self.commit = commit
        self.path = path
        self.content_hash = content_hash
        self.storage = storage
        # the server's id and reference name of the dataset, when it was resolved through the server
        self.dataset_id: Optional[str] = None
        self.reference: Optional[str] = None

    def __repr__(self) -> str:
        return f"Dataset({self.repo}@{self.commit[:10]}, {self.path})"

    def fetch_commands(self) -> List[str]:
        """How to fetch this version by hand. See :func:`fetch_commands`."""
        return fetch_commands(self.repo, self.commit, self.storage)

    @classmethod
    def get(
        cls,
        reference: str,
        commit: str,
        alias: Optional[str] = None,
        cache: Optional[DatasetCache] = None,
        record: bool = True,
    ) -> "Dataset":
        """The dataset ``reference`` names, at ``commit``, downloaded to the cache unless it is there already.

        :param reference: the dataset's id, its name (unique in the company), or the URL of its repository. The server
            resolves it; a URL of a public repository is read without the server.
        :param commit: the commit, branch or tag to use. The run records the commit it resolves to.
        :param alias: the name the run records the dataset under. Defaults to its name.
        :param cache: where to keep the files. Defaults to the user's dataset cache.
        :param record: write the dataset onto the current task (default). Callers that record it themselves pass False.
        """
        cache = cache or DatasetCache.default()
        repo, dataset_id, name = _resolve_reference(reference)
        git = mirror(cache, repo, git_token(repo, "read"))
        sha = resolve_commit(git, repo, commit)
        if not git.ok("cat-file", "-e", f"{sha}:{footprint.CONFIG_FILE}"):
            raise DatasetError(
                f"{repo} is not a CairnML dataset: {footprint.CONFIG_FILE} is missing at {sha[:10]}"
            )

        checkout = cache.checkout_dir(repo, sha)
        if not cache.is_ready(checkout):
            cls._download(cache, git, checkout, sha, repo)
        storage = _storage_of(checkout)
        if storage == "lfs":
            content = footprint.lfs_record(str(checkout))["hash"]
        else:
            dvc_file = checkout / DVC_FILE
            match = re.search(r"md5:\s*([0-9a-f]{32}(?:\.dir)?)", dvc_file.read_text()) if dvc_file.exists() else None
            content = match.group(1) if match else None
        dataset = cls(repo, sha, checkout / DATA_DIR, content, storage)
        dataset.dataset_id, dataset.reference = dataset_id, name
        cache.touch(checkout)
        cache.evict()
        if record:
            dataset._record(alias)
        return dataset

    def use(self, cache: Optional[DatasetCache] = None):
        """Hold the version in the cache while it is read, so the cache does not remove it."""
        cache = cache or DatasetCache.default()
        return cache.using(self.path.parent)

    # internals

    @staticmethod
    def _download(cache: DatasetCache, git: Git, checkout: Path, sha: str, repo: str) -> None:
        with cache.downloading():
            if checkout.exists():
                shutil.rmtree(checkout)
            git.archive_to(sha, checkout)
            if _storage_of(checkout) == "lfs":
                Dataset._materialise_lfs(git, checkout, sha, repo)
                cache.mark_ready(checkout)
                return
            dvc = Dvc(checkout)
            dvc.config("core.no_scm", "true")
            dvc.set_cache(cache.dvc_dir)
            url = dvc.remote_url()
            if url and url.startswith("s3://"):
                name = github_name(repo)
                if not name:
                    raise DatasetError(f"cannot read the repository name from {repo}")
                keys = fetch_storage_keys(repository=name, level="read")
                dvc.set_credentials(keys.access_key, keys.secret_key, keys.session_token)
            if (checkout / DVC_FILE).exists():
                try:
                    dvc.pull()
                except DatasetError as exc:
                    shutil.rmtree(checkout, ignore_errors=True)
                    raise DatasetError(f"the files of {repo}@{sha[:10]} are not in storage: push them first ({exc})")
            cache.mark_ready(checkout)

    @staticmethod
    def _materialise_lfs(git: Git, checkout: Path, sha: str, repo: str) -> None:
        """Replace the LFS pointers in the archive by their objects, fetched into the cache's copy of the repository."""
        git.lfs("fetch", "origin", sha, transfer=True)
        objects = Path(git.path) / "lfs" / "objects"
        for path in sorted((checkout / DATA_DIR).rglob("*")):
            if not path.is_file():
                continue
            with open(path, "rb") as handle:
                head = handle.read(footprint.POINTER_MAX_BYTES)
            if not head.startswith(footprint.LFS_POINTER):
                continue
            oid = re.search(rb"^oid sha256:([0-9a-f]{64})$", head, re.M)
            if not oid:
                raise DatasetError(f"{path.relative_to(checkout)} of {repo}@{sha[:10]} is not a valid LFS pointer")
            name = oid.group(1).decode()
            source = objects / name[:2] / name[2:4] / name
            if not source.is_file():
                raise DatasetError(
                    f"the content of {path.relative_to(checkout)} of {repo}@{sha[:10]} is not in the LFS storage of "
                    f"the repository: push the dataset again"
                )
            shutil.copyfile(source, path)

    def _record(self, alias: Optional[str]) -> None:
        values = {
            "kind": "local", "repo": self.repo, "commit": self.commit,
            "content_hash": self.content_hash or "", "storage": self.storage,
        }
        if self.dataset_id:
            values["id"] = self.dataset_id
        if self.reference:
            values["reference"] = self.reference
        record(alias or self.reference or default_alias(self.repo), values)

    @classmethod
    def get_external(cls, name: str, commit: Optional[str] = None, revision: Optional[str] = None, **kwargs) -> "ExternalDataset":
        """An outside dataset read through its adapter. See :func:`cairnml.datasets.external.get_external`."""
        from .external import get_external

        return get_external(name, commit, revision=revision, **kwargs)

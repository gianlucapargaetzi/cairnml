"""Using a dataset in a training script: ``Dataset.get(repo, commit)``.

The dataset is fetched into the user's cache once and reused by every later run on the machine. The run
records which repository and commit it used, and the content hash of the files.
"""

import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from . import footprint
from ._shared import default_alias, git_token, mirror, record, resolve_commit
from .cache import DatasetCache
from .credentials import fetch_storage_keys
from .dvcutil import Dvc
from .errors import DatasetError
from .gitutil import Git
from .repo import DATA_DIR, DVC_FILE

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

    def __repr__(self) -> str:
        return f"Dataset({self.repo}@{self.commit[:10]}, {self.path})"

    def fetch_commands(self) -> List[str]:
        """How to fetch this version by hand. See :func:`fetch_commands`."""
        return fetch_commands(self.repo, self.commit, self.storage)

    @classmethod
    def get(
        cls,
        repo: str,
        commit: str,
        alias: Optional[str] = None,
        cache: Optional[DatasetCache] = None,
        record: bool = True,
    ) -> "Dataset":
        """The dataset in ``repo`` at ``commit``, downloaded to the cache unless it is there already.

        :param repo: URL of the dataset repository
        :param commit: the commit, branch or tag to use. The run records the commit it resolves to.
        :param alias: the name the run records the dataset under. Defaults to the repository name.
        :param cache: where to keep the files. Defaults to the user's dataset cache.
        :param record: write the dataset onto the current task (default). Callers that record it themselves pass False.
        """
        cache = cache or DatasetCache.default()
        git = mirror(cache, repo, git_token(repo))
        sha = resolve_commit(git, repo, commit)

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
                keys = fetch_storage_keys()
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
        record(
            alias or default_alias(self.repo),
            {
                "kind": "local", "repo": self.repo, "commit": self.commit,
                "content_hash": self.content_hash or "", "storage": self.storage,
            },
        )

    @classmethod
    def get_external(cls, name: str, commit: Optional[str] = None, revision: Optional[str] = None, **kwargs) -> "ExternalDataset":
        """An outside dataset read through its adapter. See :func:`cairnml.datasets.external.get_external`."""
        from .external import get_external

        return get_external(name, commit, revision=revision, **kwargs)

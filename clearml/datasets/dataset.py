"""Using a dataset in a training script: ``Dataset.get(repo, commit)``.

The dataset is fetched into the user's cache once and reused by every later run on the machine. The run
records which repository and commit it used, and the content hash of the files.
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ._shared import default_alias, git_token, mirror, record, resolve_commit
from .cache import DatasetCache
from .credentials import fetch_storage_keys
from .dvcutil import Dvc
from .errors import DatasetError
from .gitutil import Git
from .repo import DATA_DIR, DVC_FILE

if TYPE_CHECKING:
    from .external import ExternalDataset


class Dataset:
    """One version of a local dataset, available as a folder of files."""

    def __init__(self, repo: str, commit: str, path: Path, content_hash: Optional[str]) -> None:
        self.repo = repo
        self.commit = commit
        self.path = path
        self.content_hash = content_hash

    def __repr__(self) -> str:
        return f"Dataset({self.repo}@{self.commit[:10]}, {self.path})"

    @classmethod
    def get(
        cls,
        repo: str,
        commit: str,
        alias: Optional[str] = None,
        cache: Optional[DatasetCache] = None,
    ) -> "Dataset":
        """The dataset in ``repo`` at ``commit``, downloaded to the cache unless it is there already.

        :param repo: URL of the dataset repository
        :param commit: the commit, branch or tag to use. The run records the commit it resolves to.
        :param alias: the name the run records the dataset under. Defaults to the repository name.
        :param cache: where to keep the files. Defaults to the user's dataset cache.
        """
        cache = cache or DatasetCache.default()
        git = mirror(cache, repo, git_token(repo))
        sha = resolve_commit(git, repo, commit)

        checkout = cache.checkout_dir(repo, sha)
        if not cache.is_ready(checkout):
            cls._download(cache, git, checkout, sha, repo)
        dvc_file = checkout / DVC_FILE
        match = re.search(r"md5:\s*([0-9a-f]{32}(?:\.dir)?)", dvc_file.read_text()) if dvc_file.exists() else None
        dataset = cls(repo, sha, checkout / DATA_DIR, match.group(1) if match else None)
        cache.touch(checkout)
        cache.evict()
        dataset._record(alias)
        return dataset

    def use(self, cache: Optional[DatasetCache] = None):
        """Hold the version in the cache while it is read, so the cache does not remove it."""
        cache = cache or DatasetCache.default()
        return cache.using(self.path.parent)

    # internals

    @staticmethod
    def _download(cache: DatasetCache, git: Git, checkout: Path, sha: str, repo: str) -> None:
        import shutil

        with cache.downloading():
            if checkout.exists():
                shutil.rmtree(checkout)
            git.archive_to(sha, checkout)
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

    def _record(self, alias: Optional[str]) -> None:
        record(
            alias or default_alias(self.repo),
            {"kind": "local", "repo": self.repo, "commit": self.commit, "content_hash": self.content_hash or ""},
        )

    @classmethod
    def get_external(cls, name: str, commit: Optional[str] = None, revision: Optional[str] = None, **kwargs) -> "ExternalDataset":
        """An outside dataset read through its adapter. See :func:`clearml.datasets.external.get_external`."""
        from .external import get_external

        return get_external(name, commit, revision=revision, **kwargs)

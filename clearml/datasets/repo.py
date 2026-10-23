"""A dataset repository on the user's machine: the workflow behind ``clearml-dataset``.

A dataset is a git repository with a ``data/`` folder. Git holds the metadata, DVC holds the files. The
commit of the repository is the version of the dataset.
"""

import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from .credentials import StorageKeys, fetch_storage_keys
from .dvcutil import Dvc
from .errors import DatasetError
from .gitutil import Git

DATA_DIR = "data"
DVC_FILE = "data.dvc"


def github_name(url: str) -> Optional[str]:
    """owner/name of a repository URL, from either the https or the ssh form."""
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url.strip())
    return match.group(1) if match else None


class DatasetRepo:
    def __init__(
        self,
        path: Union[str, Path] = ".",
        keys: Optional[Callable[[], StorageKeys]] = None,
    ) -> None:
        self.path = Path(path).resolve()
        self.git = Git(self.path)
        self.dvc = Dvc(self.path)
        self._keys = keys or fetch_storage_keys

    # init

    def init(self, remote_url: Optional[str] = None) -> str:
        """Set up git tracking and file storage in an existing repository that has an origin.

        With no ``remote_url`` the storage is the dataset bucket of the server, under this repository's name.
        Returns the storage URL.
        """
        if not self.git.is_repo():
            raise DatasetError(
                f"{self.path} is not a git repository: create the repository on GitHub, clone it, and run init in it"
            )
        origin = self.git.remote_url()
        if not origin:
            raise DatasetError("the repository has no origin: clone it from GitHub, or add the remote first")
        if (self.path / ".dvc").exists():
            raise DatasetError("this repository is already set up for datasets")
        endpoint = region = None
        if remote_url is None:
            keys = self._keys()
            name = github_name(origin)
            if not name:
                raise DatasetError(f"cannot read the repository name from {origin}")
            remote_url, endpoint, region = f"s3://{keys.bucket}/{name}", keys.endpoint, keys.region
        self.dvc.init()
        self.dvc.config("core.analytics", "false", local=False)
        self.dvc.set_remote(remote_url, endpoint=endpoint, region=region)
        (self.path / DATA_DIR).mkdir(exist_ok=True)
        self.git.add([".dvc", ".dvcignore"])
        return remote_url

    # status

    def status(self) -> Dict[str, object]:
        """The branch, and the data changes in three groups, like git: not added, added, and untracked."""
        self._require_setup()
        dvc = self.dvc.status()

        def files(group: Dict[str, List[str]]) -> Dict[str, List[str]]:
            return {
                kind: [p for p in group.get(kind, []) if not p.endswith("/")] for kind in ("added", "modified", "deleted")
            }

        other = [path for _i, _w, path in self.git.porcelain() if path not in (DVC_FILE, ".gitignore") and not path.startswith(".dvc")]
        return {
            "branch": self.git.branch(),
            "commit": self.git.head(),
            "untracked": [p for p in dvc.get("untracked", []) if p.startswith(f"{DATA_DIR}/")],
            "not_added": files(dvc.get("uncommitted", {})),
            "added": files(dvc.get("committed", {})),
            "other": [p for p in other if not p.startswith(f"{DATA_DIR}/")],
        }

    # add / commit / push

    def add(self) -> Dict[str, object]:
        """Record the current state of ``data/``: new and changed files are included, deleted files are removed."""
        self._require_setup()
        if not (self.path / DATA_DIR).is_dir():
            raise DatasetError(f"there is no {DATA_DIR}/ folder: copy your files into {DATA_DIR}/ first")
        self.dvc.add(DATA_DIR)
        self.git.add([DVC_FILE, ".gitignore"])
        return self.status()

    def commit(self, message: str) -> str:
        self._require_setup()
        if not self.git.has_staged():
            raise DatasetError("nothing to commit: run add first")
        return self.git.commit(message)

    def push(self) -> str:
        """Upload the files, then the commits. A commit is never published before its files."""
        self._require_setup()
        if not self.git.head():
            raise DatasetError("nothing to push: commit first")
        self._apply_keys()
        self.dvc.push()
        self.git.push()
        return self.git.head()

    def _apply_keys(self) -> None:
        url = self.dvc.remote_url()
        if url and url.startswith("s3://"):
            keys = self._keys()
            self.dvc.set_credentials(keys.access_key, keys.secret_key, keys.session_token)

    def _require_setup(self) -> None:
        if not (self.path / ".dvc").is_dir():
            raise DatasetError("this repository is not set up for datasets: run init first")

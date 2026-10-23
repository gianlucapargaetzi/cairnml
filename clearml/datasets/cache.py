"""The per-user cache of datasets.

Layout under the cache root::

    dvc/                      the content store of DVC, shared by every dataset and version of the user
    repos/<repo>.git          a bare copy of each dataset repository (metadata only)
    checkouts/<repo>/<sha>/   one dataset version: its files are links to the content store
    locks/                    lock files

A version stays on disk after use so the next run does not download it again. When the cache is over its
limit, the versions used longest ago are removed first, then the content no version links to any more.
A version in use, or a download in progress, is never touched.
"""

import hashlib
import os
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Set, Tuple, Union

try:
    import fcntl
except ImportError:  # Windows: no cross-process locks, eviction then only runs when nothing else is known to run
    fcntl = None

READY = ".ready"
LAST_USED = ".last_used"

_UNITS = {"": 1, "B": 1, "K": 1024, "KB": 1024, "M": 1024**2, "MB": 1024**2, "G": 1024**3, "GB": 1024**3,
          "T": 1024**4, "TB": 1024**4}


def parse_size(value: Union[str, int, float]) -> int:
    """Bytes from 1024, "512MB" or "50 GB"."""
    if isinstance(value, (int, float)):
        return int(value)
    match = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]*)\s*", str(value))
    if not match or match.group(2).upper() not in _UNITS:
        raise ValueError(f"not a size: {value!r} (use for example 50GB)")
    return int(float(match.group(1)) * _UNITS[match.group(2).upper()])


def repo_key(url: str) -> str:
    """A folder name for a repository URL: owner__name for GitHub style URLs, else a hash of the URL."""
    match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    if match:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", f"{match.group(1)}__{match.group(2)}")
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def _lock(path: Path, exclusive: bool, blocking: bool = True):
    """An open lock file handle holding the lock, or None when a non-blocking exclusive lock is refused."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    if fcntl is None:
        return handle
    try:
        fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | (0 if blocking else fcntl.LOCK_NB))
    except OSError:
        handle.close()
        return None
    return handle


def _unlock(handle) -> None:
    if handle is not None:
        handle.close()


class DatasetCache:
    def __init__(self, root: Union[str, Path], max_bytes: int) -> None:
        self.root = Path(root)
        self.max_bytes = max_bytes

    @classmethod
    def default(cls) -> "DatasetCache":
        from ..config import config, get_cache_dir

        root = config.get("sdk.datasets.cache.dir", None) or config.get("datasets.cache.dir", None)
        size = config.get("sdk.datasets.cache.max_size", None) or config.get("datasets.cache.max_size", None)
        return cls(Path(os.path.expanduser(root)) if root else get_cache_dir() / "datasets", parse_size(size or "50GB"))

    @property
    def dvc_dir(self) -> Path:
        return self.root / "dvc"

    def repo_dir(self, url: str) -> Path:
        return self.root / "repos" / f"{repo_key(url)}.git"

    def checkout_dir(self, url: str, commit: str) -> Path:
        return self.root / "checkouts" / repo_key(url) / commit

    def is_ready(self, checkout: Path) -> bool:
        return (checkout / READY).exists()

    def mark_ready(self, checkout: Path) -> None:
        (checkout / READY).touch()
        self.touch(checkout)

    def touch(self, checkout: Path) -> None:
        (checkout / LAST_USED).touch()

    # Locks

    @contextmanager
    def downloading(self) -> Iterator[None]:
        """Held while content is fetched into the store; eviction waits for it."""
        handle = _lock(self.root / "locks" / "store.lock", exclusive=False)
        try:
            yield
        finally:
            _unlock(handle)

    @contextmanager
    def using(self, checkout: Path) -> Iterator[None]:
        """Held while a version is read; eviction leaves it alone."""
        handle = _lock(self.root / "locks" / f"use-{checkout.parent.name}-{checkout.name}.lock", exclusive=False)
        try:
            self.touch(checkout)
            yield
        finally:
            _unlock(handle)

    # Size and eviction

    def checkouts(self) -> List[Path]:
        base = self.root / "checkouts"
        if not base.is_dir():
            return []
        return [c for repo in base.iterdir() if repo.is_dir() for c in repo.iterdir() if c.is_dir()]

    @staticmethod
    def _files(directory: Path) -> Iterator[Path]:
        for folder, _dirs, names in os.walk(directory):
            for name in names:
                yield Path(folder) / name

    def _sizes(self) -> Dict[Tuple[int, int], int]:
        """Size of every distinct file under the store and the checkouts, by device and inode."""
        sizes: Dict[Tuple[int, int], int] = {}
        for directory in [self.dvc_dir, self.root / "checkouts"]:
            for path in self._files(directory):
                try:
                    info = os.lstat(path)
                except OSError:
                    continue
                sizes[(info.st_dev, info.st_ino)] = info.st_size
        return sizes

    def usage(self) -> int:
        return sum(self._sizes().values())

    def _referenced(self) -> Set[Tuple[int, int]]:
        """Content files some checkout links to (through a hard link or a symbolic link)."""
        used: Set[Tuple[int, int]] = set()
        for checkout in self.checkouts():
            for path in self._files(checkout):
                try:
                    info = os.stat(path)
                except OSError:
                    continue
                used.add((info.st_dev, info.st_ino))
        return used

    def _remove_unreferenced_content(self) -> None:
        referenced = self._referenced()
        for path in list(self._files(self.dvc_dir / "files")):
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if (info.st_dev, info.st_ino) not in referenced:
                path.unlink()

    def evict(self) -> int:
        """Bring the cache under its limit; returns the number of versions removed."""
        if self.usage() <= self.max_bytes:
            return 0
        guard = _lock(self.root / "locks" / "store.lock", exclusive=True, blocking=False)
        if guard is None:  # a download is in progress; try again after it
            return 0
        removed = 0
        try:
            candidates = sorted(
                self.checkouts(),
                key=lambda c: (c / LAST_USED).stat().st_mtime if (c / LAST_USED).exists() else 0,
            )
            for checkout in candidates:
                lock = _lock(self.root / "locks" / f"use-{checkout.parent.name}-{checkout.name}.lock",
                             exclusive=True, blocking=False)
                if lock is None:  # in use
                    continue
                try:
                    shutil.rmtree(checkout, ignore_errors=True)
                    removed += 1
                finally:
                    _unlock(lock)
                self._remove_unreferenced_content()
                if self.usage() <= self.max_bytes:
                    break
        finally:
            _unlock(guard)
        return removed

    def clear(self) -> int:
        """Remove every version that is not in use."""
        saved, self.max_bytes = self.max_bytes, -1
        try:
            return self.evict()
        finally:
            self.max_bytes = saved

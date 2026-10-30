"""Where an outside dataset comes from, and how to get exactly one revision of it.

``resolve`` turns what the run asked for (nothing, a branch, a sha) into the revision that will be used, and
``fetch`` downloads that revision into a folder. The revision is recorded on the run.
"""

import gzip
import hashlib
import os
import re
import shutil
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Optional

from . import process
from .errors import DatasetError

_SHA = re.compile(r"[0-9a-f]{40}")


def _git_env() -> Dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # a missing credential is an error to report, not a question to ask
    return env


class Source:
    kind = ""
    downloads = True  # False: the files are used where they are

    def resolve(self, link: str, revision: Optional[str]) -> str:
        raise NotImplementedError

    def fetch(self, link: str, revision: str, dest: Path) -> None:
        raise NotImplementedError

    def local_root(self, link: str) -> Path:
        raise NotImplementedError


class HuggingFace(Source):
    """A dataset repository on the Hugging Face Hub. Its revisions are git commits."""

    kind = "huggingface"

    @staticmethod
    def _repo_id(link: str) -> str:
        link = re.sub(r"^(hf://datasets/|https?://huggingface\.co/datasets/)", "", link.strip()).strip("/")
        if not re.fullmatch(r"[\w.-]+(/[\w.-]+)?", link):
            raise DatasetError(f"not a Hugging Face dataset: {link!r} (use for example hf://datasets/org/name)")
        return link

    @staticmethod
    def _hub():
        try:
            import huggingface_hub
        except ImportError:
            raise DatasetError("huggingface_hub is not installed: pip install huggingface_hub") from None
        return huggingface_hub

    def resolve(self, link: str, revision: Optional[str]) -> str:
        if revision and _SHA.fullmatch(revision):
            return revision
        info = self._hub().HfApi().dataset_info(self._repo_id(link), revision=revision)
        return info.sha

    def fetch(self, link: str, revision: str, dest: Path) -> None:
        self._hub().snapshot_download(
            repo_id=self._repo_id(link), repo_type="dataset", revision=revision, local_dir=str(dest)
        )


class GitSource(Source):
    """Any git repository that holds the files of a dataset."""

    kind = "git"

    def resolve(self, link: str, revision: Optional[str]) -> str:
        if revision and _SHA.fullmatch(revision):
            return revision
        ref = revision or "HEAD"
        result = process.run(["git", "ls-remote", link, ref], env=_git_env())
        lines = result.stdout.split()
        if result.returncode != 0 or not lines:
            raise DatasetError(f"cannot find {ref!r} in {link}: {result.stderr.strip()}")
        return lines[0]

    def fetch(self, link: str, revision: str, dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        for args in (["init", "-q"], ["fetch", "-q", "--depth", "1", link, revision], ["checkout", "-q", "FETCH_HEAD"]):
            result = process.run(["git", "-C", str(dest)] + args, env=_git_env(), timeout=process.transfer_timeout())
            if result.returncode != 0:
                raise DatasetError(f"cannot fetch {revision[:10]} of {link}: {result.stderr.strip()}")


class LocalFolder(Source):
    """A folder with no revision history. The revision is a hash of what is in it."""

    kind = "local"
    downloads = False

    def local_root(self, link: str) -> Path:
        path = Path(link.replace("file://", "", 1)).expanduser().resolve()
        if not path.is_dir():
            raise DatasetError(f"{path} is not a folder")
        return path

    def resolve(self, link: str, revision: Optional[str]) -> str:
        root = self.local_root(link)
        digest = hashlib.sha256()
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            digest.update(str(path.relative_to(root)).encode())
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(block)
        found = digest.hexdigest()
        if revision and revision != found:
            raise DatasetError(f"{root} no longer matches the requested content hash {revision[:12]}")
        return found


UNPACK_SUFFIXES = {"zip": ".zip", "tar.gz": ".tar.gz", "gz": ".gz"}
UNPACK_HOWS = ("none",) + tuple(UNPACK_SUFFIXES)
CHECKSUM_LENGTHS = {"md5": 32, "sha256": 64}  # hex digits


def _fetch_entry(item, where: str) -> dict:
    """One file of a web dataset: ``{"url", "checksum", "unpack"}``.

    An item is an address, or a mapping with ``url`` and optionally ``checksum`` (``md5:<hex>`` or ``sha256:<hex>``,
    the value the publisher gives) and ``unpack`` (``none``, ``zip``, ``tar.gz`` or ``gz``). An address without
    ``unpack`` is unpacked if it ends in ``.zip``, as it always was.
    """
    if isinstance(item, str):
        item = {"url": item}
    if not isinstance(item, dict):
        raise DatasetError(f"{where}: a fetch entry is an address, or a mapping with url, checksum and unpack")
    unknown = sorted(set(item) - {"url", "checksum", "unpack"})
    if unknown:
        raise DatasetError(f"{where}: unknown key {', '.join(unknown)} in a fetch entry (use url, checksum, unpack)")
    url = item.get("url")
    if not isinstance(url, str) or not re.match(r"(https?|file)://", url):
        raise DatasetError(f"{where}: not a web address: {url!r}")
    checksum = item.get("checksum")
    if checksum is not None:
        algorithm, _, value = str(checksum).partition(":")
        length = CHECKSUM_LENGTHS.get(algorithm)
        if length is None or not re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", value):
            raise DatasetError(
                f"{where}: the checksum of {url} must be md5:<32 hex digits> or sha256:<64 hex digits>, "
                f"not {checksum!r}"
            )
        checksum = f"{algorithm}:{value.lower()}"
    unpack = item.get("unpack")
    if unpack is None:
        unpack = "zip" if url.split("?", 1)[0].endswith(".zip") else "none"
    if unpack not in UNPACK_HOWS:
        raise DatasetError(f"{where}: unpack of {url} must be one of {', '.join(UNPACK_HOWS)}, not {unpack!r}")
    return {"url": url, "checksum": checksum, "unpack": unpack}


def parse_fetch(items, where: str) -> list:
    """The ``fetch`` list of a pin file, checked. ``where`` names the file in the messages."""
    if not isinstance(items, list) or not items:
        raise DatasetError(f"{where}: fetch must be a non-empty list of files")
    return [_fetch_entry(item, where) for item in items]


def _verify_checksum(path: Path, checksum: str, url: str) -> None:
    algorithm, _, expected = checksum.partition(":")
    digest = hashlib.new(algorithm)
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise DatasetError(
            f"{path.name} from {url} does not match its {algorithm} checksum (expected {expected}, got "
            f"{digest.hexdigest()}): the download is refused"
        )


def _unpack(target: Path, how: str) -> None:
    if how == "none":
        return
    suffix = UNPACK_SUFFIXES[how]
    if not target.name.endswith(suffix):
        raise DatasetError(f"{target.name} is not a {suffix} file, so it cannot be unpacked as {how}")
    stem = target.name[: -len(suffix)]
    if how == "zip":
        with zipfile.ZipFile(target) as archive:
            archive.extractall(target.parent / stem)
    elif how == "tar.gz":
        with tarfile.open(target, mode="r:gz") as archive:
            if hasattr(tarfile, "data_filter"):  # Python 3.12+: refuse links and paths that leave the folder
                archive.extractall(target.parent / stem, filter="data")
            else:
                archive.extractall(target.parent / stem)
    else:  # gz: one file, written in place of the archive
        with gzip.open(target, "rb") as source, open(target.with_name(stem), "wb") as out:
            shutil.copyfileobj(source, out)
    target.unlink()


class Web(Source):
    """Files on the web: one address, a list of them, or a ``fetch`` list (see :func:`parse_fetch`).

    A zip archive is unpacked into a folder named after it (``images.zip`` becomes ``images/``) and the archive is
    removed. A file with a checksum is verified after download, and a mismatch names the file and refuses the download.
    The revision is a hash of the checksums when every file has one; a file without one contributes the ETag and size
    the server reports, so the revision changes when that file changes.
    """

    kind = "web"

    @staticmethod
    def _entries(link) -> list:
        items = [link] if isinstance(link, (str, dict)) else list(link)
        return [_fetch_entry(item, "the web source") for item in items]

    def resolve(self, link: str, revision: Optional[str]) -> str:
        entries = self._entries(link)  # checked first, so a bad entry is reported whatever the revision
        if revision:
            return revision
        digest = hashlib.sha256()
        for entry in entries:
            if entry["checksum"]:
                digest.update(f"{entry['checksum']}\n".encode())
                continue
            request = urllib.request.Request(entry["url"], method="HEAD")
            try:
                with urllib.request.urlopen(request, timeout=60) as reply:
                    etag = reply.headers.get("ETag", "")
                    size = reply.headers.get("Content-Length", "")
            except OSError as exc:
                raise DatasetError(f"cannot reach {entry['url']}: {exc}") from None
            digest.update(f"{entry['url']}\n{etag}\n{size}\n".encode())
        return digest.hexdigest()

    def fetch(self, link: str, revision: str, dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        for entry in self._entries(link):
            url = entry["url"]
            target = dest / url.rsplit("/", 1)[-1].split("?", 1)[0]
            try:
                urllib.request.urlretrieve(url, target)
            except OSError as exc:
                raise DatasetError(f"cannot download {url}: {exc}") from None
            if entry["checksum"]:
                _verify_checksum(target, entry["checksum"], url)
            _unpack(target, entry["unpack"])


SOURCES = {cls.kind: cls for cls in (HuggingFace, GitSource, LocalFolder, Web)}


def source_for(kind: str) -> Source:
    if kind not in SOURCES:
        raise DatasetError(f"unknown source {kind!r}; known: {', '.join(SOURCES)}")
    return SOURCES[kind]()

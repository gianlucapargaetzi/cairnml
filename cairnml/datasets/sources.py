"""Where an outside dataset comes from, and how to get exactly one revision of it.

``resolve`` turns what the run asked for (nothing, a branch, a sha) into the revision that will be used, and
``fetch`` downloads that revision into a folder. The revision is recorded on the run.
"""

import hashlib
import os
import re
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


class Web(Source):
    """Files on the web: one address, or a list of them. A zip archive is unpacked into a folder named after it
    (``images.zip`` becomes ``images/``) and the archive is removed. The revision is a hash of the ETags and sizes
    the server reports, so it changes when a file changes."""

    kind = "web"

    @staticmethod
    def _urls(link) -> list:
        urls = [link] if isinstance(link, str) else list(link)
        for url in urls:
            if not re.match(r"(https?|file)://", url):
                raise DatasetError(f"not a web address: {url!r}")
        return urls

    def resolve(self, link: str, revision: Optional[str]) -> str:
        if revision:
            return revision
        digest = hashlib.sha256()
        for url in self._urls(link):
            request = urllib.request.Request(url, method="HEAD")
            try:
                with urllib.request.urlopen(request, timeout=60) as reply:
                    etag = reply.headers.get("ETag", "")
                    size = reply.headers.get("Content-Length", "")
            except OSError as exc:
                raise DatasetError(f"cannot reach {url}: {exc}") from None
            digest.update(f"{url}\n{etag}\n{size}\n".encode())
        return digest.hexdigest()

    def fetch(self, link: str, revision: str, dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        for url in self._urls(link):
            target = dest / url.rsplit("/", 1)[-1].split("?", 1)[0]
            try:
                urllib.request.urlretrieve(url, target)
            except OSError as exc:
                raise DatasetError(f"cannot download {url}: {exc}") from None
            if target.suffix == ".zip":
                with zipfile.ZipFile(target) as archive:
                    archive.extractall(dest / target.stem)
                target.unlink()


SOURCES = {cls.kind: cls for cls in (HuggingFace, GitSource, LocalFolder, Web)}


def source_for(kind: str) -> Source:
    if kind not in SOURCES:
        raise DatasetError(f"unknown source {kind!r}; known: {', '.join(SOURCES)}")
    return SOURCES[kind]()

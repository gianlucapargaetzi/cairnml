"""The git command line, for the few things the dataset tool needs from it."""

import base64
import os
import subprocess
import tarfile
from pathlib import Path
from typing import List, Optional, Sequence, Union

from .errors import DatasetError


class GitError(DatasetError):
    pass


def _env() -> dict:
    env = dict(os.environ)
    # Never block on a prompt: a missing credential is an error to report, not a question to ask.
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


class Git:
    """One repository, or one bare repository when ``bare`` is True.

    ``token`` is a GitHub installation token. It is sent as an HTTP header for the one command, never
    stored in the repository's configuration or in the remote URL.
    """

    def __init__(self, path: Union[str, Path], token: Optional[str] = None, bare: bool = False) -> None:
        self.path = Path(path)
        self.token = token
        self.bare = bare

    def _base(self) -> List[str]:
        args = ["git"]
        if self.token:
            basic = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
            args += ["-c", f"http.extraheader=Authorization: Basic {basic}"]
        args += ["--git-dir", str(self.path)] if self.bare else ["-C", str(self.path)]
        return args

    def run(self, *args: str, check: bool = True) -> str:
        try:
            result = subprocess.run(
                self._base() + list(args), capture_output=True, text=True, env=_env(), check=False
            )
        except FileNotFoundError:
            raise GitError("git is not installed") from None
        if check and result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
        return result.stdout.strip()

    def ok(self, *args: str) -> bool:
        """True when the git command exits with 0."""
        try:
            return subprocess.run(
                self._base() + list(args), capture_output=True, env=_env(), check=False
            ).returncode == 0
        except FileNotFoundError:
            raise GitError("git is not installed") from None

    @staticmethod
    def init(path: Union[str, Path]) -> "Git":
        Path(path).mkdir(parents=True, exist_ok=True)
        Git(path).run("init", "-q")
        return Git(path)

    def is_repo(self) -> bool:
        return self.path.is_dir() and self.ok("rev-parse", "--git-dir")

    def branch(self) -> str:
        return self.run("symbolic-ref", "--short", "-q", "HEAD", check=False) or "HEAD"

    def head(self) -> Optional[str]:
        return self.run("rev-parse", "--verify", "-q", "HEAD", check=False) or None

    def remote_url(self, name: str = "origin") -> Optional[str]:
        return self.run("remote", "get-url", name, check=False) or None

    def porcelain(self) -> List[tuple]:
        """(index status, worktree status, path) for every changed or untracked path."""
        out = self.run("status", "--porcelain", "-z", "--untracked-files=all")
        entries = [e for e in out.split("\0") if e]
        rows = []
        skip = False
        for entry in entries:
            if skip:  # the old name of a rename follows the new one
                skip = False
                continue
            rows.append((entry[0], entry[1], entry[3:]))
            if entry[0] in "RC":
                skip = True
        return rows

    def add(self, paths: Sequence[str], force: bool = False) -> None:
        self.run("add", *(["-f"] if force else []), "--", *paths)

    def commit(self, message: str) -> str:
        self.run("commit", "-q", "-m", message)
        return self.run("rev-parse", "HEAD")

    def has_staged(self) -> bool:
        return not self.ok("diff", "--cached", "--quiet")

    def push(self) -> None:
        branch = self.branch()
        if self.run("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False):
            self.run("push", "-q")
        else:
            self.run("push", "-q", "--set-upstream", "origin", branch)

    def is_pushed(self, commit: str = "HEAD") -> bool:
        return bool(self.run("branch", "-r", "--contains", commit, check=False))

    # The bare mirror of a remote repository, kept in the cache.

    @staticmethod
    def clone_bare(url: str, dest: Union[str, Path], token: Optional[str] = None) -> "Git":
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        git = Git(dest, token=token, bare=True)
        base = ["git"]
        if token:
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            base += ["-c", f"http.extraheader=Authorization: Basic {basic}"]
        result = subprocess.run(
            base + ["clone", "-q", "--bare", url, str(dest)], capture_output=True, text=True, env=_env()
        )
        if result.returncode != 0:
            raise GitError(f"could not clone {url}: {result.stderr.strip()}")
        return git

    def fetch_all(self) -> None:
        self.run("fetch", "-q", "origin", "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")

    def has_commit(self, commit: str) -> bool:
        return self.ok("cat-file", "-e", f"{commit}^{{commit}}")

    def resolve(self, commit: str) -> str:
        return self.run("rev-parse", "--verify", f"{commit}^{{commit}}")

    def archive_to(self, commit: str, dest: Union[str, Path]) -> None:
        """Extract the files of one commit into ``dest``. No history, no .git folder."""
        Path(dest).mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            self._base() + ["archive", "--format=tar", commit],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_env(),
        )
        with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
            if hasattr(tarfile, "data_filter"):  # Python 3.12+: refuse links and paths that leave dest
                archive.extractall(str(dest), filter="data")
            else:
                archive.extractall(str(dest))
        stderr = proc.stderr.read().decode().strip()
        if proc.wait() != 0:
            raise GitError(f"git archive {commit} failed: {stderr}")

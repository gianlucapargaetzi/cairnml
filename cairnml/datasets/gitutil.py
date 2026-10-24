"""The git command line, for the few things the dataset tool needs from it."""

import base64
import io
import os
import tarfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

from . import process
from .errors import DatasetError


class GitError(DatasetError):
    pass


def _env(token: Optional[str] = None) -> Dict[str, str]:
    """The environment of one git command.

    The token travels as git configuration in the environment (GIT_CONFIG_COUNT and its keys), not as a ``-c`` option,
    so it does not show up in the command line that other users of the machine can read with ``ps``.
    """
    env = dict(os.environ)
    # Never block on a prompt: a missing credential is an error to report, not a question to ask.
    env["GIT_TERMINAL_PROMPT"] = "0"
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        index = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
        env["GIT_CONFIG_COUNT"] = str(index + 1)
        env[f"GIT_CONFIG_KEY_{index}"] = "http.extraheader"
        env[f"GIT_CONFIG_VALUE_{index}"] = f"Authorization: Basic {basic}"
    return env


class Git:
    """One repository, or one bare repository when ``bare`` is True.

    ``token`` is a GitHub installation token. It is sent as an HTTP header for the one command (through the
    environment of that command), never stored in the repository's configuration, the remote URL or the command line.
    """

    def __init__(self, path: Union[str, Path], token: Optional[str] = None, bare: bool = False) -> None:
        self.path = Path(path)
        self.token = token
        self.bare = bare

    def _base(self) -> List[str]:
        return ["git"] + (["--git-dir", str(self.path)] if self.bare else ["-C", str(self.path)])

    def run(self, *args: str, check: bool = True) -> str:
        try:
            result = process.run(self._base() + list(args), env=_env(self.token))
        except FileNotFoundError:
            raise GitError("git is not installed") from None
        if check and result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
        return result.stdout.strip()

    def ok(self, *args: str) -> bool:
        """True when the git command exits with 0."""
        try:
            return process.run(self._base() + list(args), env=_env(self.token), text=False).returncode == 0
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

    def tracked_sizes(self) -> dict:
        """Size in bytes of every file tracked by git (as in the working tree), by path."""
        out = self.run("ls-files", "-z")
        sizes = {}
        for path in [p for p in out.split("\0") if p]:
            full = self.path / path
            if full.is_file():
                sizes[path] = full.stat().st_size
        return sizes

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

    def push_to(self, branch: str, source: str = "HEAD") -> None:
        """Push a commit to a branch of origin, creating it."""
        self.run("push", "-q", "origin", f"{source}:refs/heads/{branch}")

    def remote_heads(self) -> List[str]:
        """Branch names that exist on origin."""
        out = self.run("ls-remote", "--heads", "origin", check=False)
        return [line.split("refs/heads/", 1)[1] for line in out.splitlines() if "refs/heads/" in line]

    def default_branch(self) -> str:
        """The branch origin treats as its default; main if that cannot be told."""
        out = self.run("ls-remote", "--symref", "origin", "HEAD", check=False)
        for line in out.splitlines():
            if line.startswith("ref:"):
                return line.split("refs/heads/", 1)[1].split()[0]
        return "main"

    def remote_contains(self, commit: str, branch: str) -> bool:
        """True when origin/<branch>, as last fetched, holds the commit."""
        return self.ok("merge-base", "--is-ancestor", commit, f"origin/{branch}")

    def fetch_origin(self) -> None:
        self.run("fetch", "-q", "origin", check=False)

    def is_pushed(self, commit: str = "HEAD") -> bool:
        return bool(self.run("branch", "-r", "--contains", commit, check=False))

    # The bare mirror of a remote repository, kept in the cache.

    @staticmethod
    def clone_bare(url: str, dest: Union[str, Path], token: Optional[str] = None) -> "Git":
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        git = Git(dest, token=token, bare=True)
        try:
            result = process.run(
                ["git", "clone", "-q", "--bare", url, str(dest)], env=_env(token), timeout=process.transfer_timeout()
            )
        except FileNotFoundError:
            raise GitError("git is not installed") from None
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
        try:
            proc = process.run(self._base() + ["archive", "--format=tar", commit], env=_env(self.token), text=False)
        except FileNotFoundError:
            raise GitError("git is not installed") from None
        if proc.returncode != 0:
            raise GitError(f"git archive {commit} failed: {proc.stderr.decode(errors='replace').strip()}")
        # A dataset repository holds metadata only (the footprint check keeps every file small), so the archive is read
        # from memory; that lets the command have a time limit like every other one.
        with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as archive:
            if hasattr(tarfile, "data_filter"):  # Python 3.12+: refuse links and paths that leave dest
                archive.extractall(str(dest), filter="data")
            else:
                archive.extractall(str(dest))

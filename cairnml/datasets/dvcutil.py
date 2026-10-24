"""The dvc command line, for the few things the dataset tool needs from it.

dvc holds the file content in object storage and in a local cache. The tool never asks the user to run it.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

from . import process
from .errors import DatasetError

REMOTE_NAME = "storage"


class DvcError(DatasetError):
    pass


_TRANSFERS = {"push", "pull", "fetch", "checkout", "get", "update"}


def _command() -> List[str]:
    found = shutil.which("dvc")
    if found:
        return [found]
    return [sys.executable, "-m", "dvc"]


class Dvc:
    def __init__(self, path: Union[str, Path], env: Optional[Dict[str, str]] = None) -> None:
        self.path = Path(path)
        self.env = env or {}

    def run(self, *args: str) -> str:
        env = dict(os.environ)
        env.update(self.env)
        env["DVC_NO_ANALYTICS"] = "true"
        # Moving data takes as long as the data is big; everything else is a short command.
        limit = process.transfer_timeout() if args and args[0] in _TRANSFERS else -1
        try:
            result = process.run(_command() + list(args), cwd=str(self.path), env=env, timeout=limit)
        except FileNotFoundError:
            raise DvcError("dvc is not installed: pip install 'cairnml[dataset]'") from None
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            if "No module named dvc" in message:
                raise DvcError("dvc is not installed: pip install 'cairnml[dataset]'")
            raise DvcError(f"dvc {' '.join(args)} failed: {message}")
        return result.stdout.strip()

    def init(self, no_scm: bool = False) -> None:
        self.run("init", "-q", *(["--no-scm"] if no_scm else []))
        self.run("config", "core.autostage", "true")

    def config(self, key: str, value: str, local: bool = True) -> None:
        self.run("config", *(["--local"] if local else []), key, value)

    def set_cache(self, directory: Union[str, Path]) -> None:
        """Share one content store between all checkouts of a user, and link files from it instead of copying."""
        self.config("cache.dir", str(directory))
        self.config("cache.type", "hardlink,symlink,copy")

    def set_remote(self, url: str, endpoint: Optional[str] = None, region: Optional[str] = None) -> None:
        self.run("remote", "add", "--force", "--default", REMOTE_NAME, url)
        if endpoint:
            self.run("remote", "modify", REMOTE_NAME, "endpointurl", endpoint)
        if region:
            self.run("remote", "modify", REMOTE_NAME, "region", region)

    def set_credentials(self, access_key: str, secret_key: str, session_token: str) -> None:
        """Keep the temporary keys in the local, untracked configuration only."""
        for key, value in (
            ("access_key_id", access_key),
            ("secret_access_key", secret_key),
            ("session_token", session_token),
        ):
            self.run("remote", "modify", "--local", REMOTE_NAME, key, value)

    def remote_url(self) -> Optional[str]:
        out = self.run("config", f"remote.{REMOTE_NAME}.url") if self._has_remote() else ""
        return out or None

    def _has_remote(self) -> bool:
        return REMOTE_NAME in self.run("remote", "list").split()

    def add(self, path: str) -> None:
        self.run("add", "-q", path)

    def status(self) -> Dict[str, Dict[str, List[str]]]:
        """Changes of the tracked data, as {"untracked": [...], "uncommitted": {...}, "committed": {...}}."""
        out = self.run("data", "status", "--granular", "--untracked-files", "--json")
        return json.loads(out) if out else {}

    def push(self) -> None:
        self.run("push", "-q")

    def pull(self, *targets: str) -> None:
        self.run("pull", "-q", *targets)

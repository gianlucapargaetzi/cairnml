"""A dataset repository on the user's machine: the workflow behind ``cairnml dataset``.

A dataset is a git repository with a ``data/`` folder. Git holds the metadata. The files are held either by DVC in an
S3 bucket of the server (``storage: s3``, the default) or by the GitHub LFS of the dataset repository (``storage: lfs``,
which needs no DVC and no storage keys). The storage is chosen at ``init`` and recorded in ``dataset.yaml``. The commit
of the repository is the version of the dataset.

Every commit carries a footprint (``dataset.lock.json``, see :mod:`cairnml.datasets.footprint`): the record that the
data was checked, in the format ``dataset.yaml`` declares. ``push`` refuses a commit whose footprint does not hold, and
publishes through a branch, because the default branch of a dataset repository accepts only pull requests that pass the
footprint check.
"""

import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from ..version import __version__
from . import footprint, process
from .cache import DatasetCache
from .check import run_check
from ._shared import git_token, on_github
from .credentials import (
    StorageKeys, fetch_dataset_list, fetch_person_git_token, fetch_storage_keys, register_dataset,
    set_dataset_credential,
)
from .dvcutil import Dvc
from .errors import DatasetError
from .gitutil import Git

DATA_DIR = "data"
DEFAULT_BRANCH = "main"
DVC_FILE = footprint.DVC_FILE
WORKFLOW_FILE = ".github/workflows/dataset.yml"
IGNORED = ("__pycache__/",)  # bytecode is never part of a dataset: init puts these in the ignore lists of the repository

WORKFLOW = """name: dataset

# The default branch accepts a change only through a pull request that passes this job (set it as a required check).
# The job reads the files in git only: it needs no data and no access to the server.
on:
  pull_request:
  push:
    branches: [main, master]

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python .cairnml/verify.py
"""


def github_name(url: str) -> Optional[str]:
    """owner/name of a repository URL, from either the https or the ssh form."""
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url.strip())
    return match.group(1) if match else None


def create_repository(name: str, private: bool = True) -> str:
    """Create an empty repository on GitHub, through the GitHub command line, and return its address for git.

    The GitHub login of the command line is used; nothing is stored by this tool.
    """
    if shutil.which("gh") is None:
        raise DatasetError(
            "the GitHub command line (gh) is not installed: install it from https://cli.github.com and run gh auth login"
        )
    try:
        made = process.run(["gh", "repo", "create", name, "--private" if private else "--public"])
    except process.CommandTimeout as exc:
        raise DatasetError(str(exc)) from None
    if made.returncode != 0:
        raise DatasetError(f"gh repo create {name} failed: {(made.stderr or made.stdout).strip()}")
    lines = made.stdout.strip().splitlines()
    url = lines[-1].strip() if lines else ""
    if not url.startswith("https://github.com/"):
        raise DatasetError(f"gh repo create {name} did not print the address of the repository: {url!r}")
    return url + ".git"


def protection_body() -> Dict[str, Any]:
    """The branch protection that create sets on the default branch (the body of the GitHub API call)."""
    return {
        "required_status_checks": None,  # no check is required: a required check that never reports blocks merges
        "enforce_admins": False,  # administrators are not held to the review either
        "required_pull_request_reviews": {
            "required_approving_review_count": 0,  # pull requests are required, no approval is
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": False,
        },
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
    }


def protection_problems(protection: Dict[str, Any]) -> List[str]:
    """What is missing from the branch protection of a dataset repository, as sentences. Empty when it is right."""
    problems = []
    if protection.get("required_pull_request_reviews") is None:  # present, even with no approvals asked
        problems.append("pull requests are not required before merging")
    if (protection.get("allow_force_pushes") or {}).get("enabled"):
        problems.append("force pushes are allowed")
    if (protection.get("allow_deletions") or {}).get("enabled"):
        problems.append("deleting the branch is allowed")
    return problems


class DatasetRepo:
    def __init__(
        self,
        path: Union[str, Path] = ".",
        keys: Optional[Callable[..., StorageKeys]] = None,
        cache: Optional[DatasetCache] = None,
    ) -> None:
        self.path = Path(path).resolve()
        self._cache = cache
        self.git = Git(self.path)
        self.dvc = Dvc(self.path)
        self._keys = keys or fetch_storage_keys
        self.warnings: List[str] = []

    def storage(self) -> Optional[str]:
        """``s3`` or ``lfs``, as ``dataset.yaml`` says, or None before :meth:`remote_add` chose it. A repository that has
        a ``.dvc`` folder but no storage line was set up before the choice was recorded, and is ``s3``."""
        config_file = self.path / footprint.CONFIG_FILE
        if not config_file.is_file():
            return None
        try:
            config = footprint.parse_config(config_file.read_text())
            if "storage" not in config and (self.path / ".dvc").is_dir():
                return "s3"
            return footprint.storage_kind(config) if "storage" in config else None
        except ValueError as exc:
            raise DatasetError(str(exc)) from None

    def _require_storage(self) -> str:
        storage = self.storage()
        if storage is None:
            raise DatasetError("no storage is chosen yet: run 'cairnml dataset remote add <url>' first")
        return storage

    def _require_remote(self) -> str:
        """The dataset is connected: it has an origin and its storage is chosen. Only ``remote add`` does this, so
        add, commit and push wait for it. Returns the storage."""
        storage = self._require_storage()
        if not self.git.remote_url():
            raise DatasetError("no remote is configured: run 'cairnml dataset remote add <url>' first")
        return storage

    # init

    def init(self, adapter: Optional[str] = None, registry: Optional[str] = None) -> None:
        """Set up a dataset in a folder of this machine, with no remote: a git repository (created if the folder is not
        one) and the format or adapter that reads ``data/``. Nothing is sent anywhere and no storage is chosen; that is
        what :meth:`remote_add` does. ``adapter="local"`` takes ``adapter.py`` at the top of the repository, which needs no
        ``registry``.
        """
        if adapter == footprint.LOCAL_ADAPTER and registry:
            raise DatasetError(
                f"adapter {footprint.LOCAL_ADAPTER} reads {footprint.ADAPTER_FILE} in this repository, so a registry does "
                f"not apply to it: leave out --registry"
            )
        if (self.path / ".dvc").exists() or (self.path / footprint.CONFIG_FILE).exists():
            raise DatasetError("this repository is already set up for datasets")
        self.path.mkdir(parents=True, exist_ok=True)
        if not self.git.is_repo():
            self.git.run("init", "-q")
            self.git.run("symbolic-ref", "HEAD", f"refs/heads/{DEFAULT_BRANCH}")
        (self.path / DATA_DIR).mkdir(exist_ok=True)

        lines = [
            "# How the files in data/ are read into the standard format. The storage is added by remote add.\n",
        ]
        if adapter:
            lines.append(f"adapter: {adapter}\n")
            if registry:
                lines.append(f"registry: {registry}\n")
        else:
            lines.append("format: native\n")
        (self.path / footprint.CONFIG_FILE).write_text("".join(lines))
        self._ignore(".gitignore")
        self._write_tooling()
        self.git.add([footprint.CONFIG_FILE, ".cairnml", ".github", ".gitignore"])

    def remote_add(
        self,
        url: str,
        lfs: bool = False,
        *,
        credential: Optional[str] = None,
        storage_url: Optional[str] = None,
        register: Callable[[str, Optional[str]], dict] = register_dataset,
        check_access: Optional[Callable[[str, str], Optional[str]]] = None,
    ) -> str:
        """Make the GitHub repository ``url`` the origin of this dataset, choose its storage, and register it with the
        server, so that its owner can push to it. Returns where the files are stored.

        Then the repository is checked for reachability with the login of the person signed in (a read token from the
        server). When it cannot be reached, nothing fails: the warning is in ``self.warnings``, with the advice to add
        credentials for the dataset, and to install the organisation's GitHub App on the repository.

        The storage is DVC in the server's dataset bucket by default, or the GitHub LFS of the repository with
        ``lfs=True``. It is written to ``dataset.yaml`` and cannot be changed afterwards. ``storage_url`` replaces the
        server's bucket for DVC (for tests and private setups). The server refusing the registration stops this before
        anything is changed here.

        ``credential`` is the access the server reads the dataset with (see credentials.list_credentials). Without one the
        dataset is public: a private one then cannot be read until a credential is added (``add_credential``), and the
        warning in ``self.warnings`` says so.
        """
        self._require_setup()
        if self.storage() is not None:
            raise DatasetError(f"this dataset already has its storage ({self.storage()}): remote add runs once")
        if self.git.remote_url():
            raise DatasetError("the repository already has an origin: remote add runs once")
        if lfs and storage_url is not None:
            raise DatasetError("storage_url applies to s3 storage only: an lfs dataset keeps its files in the GitHub LFS")
        if lfs and not self.git.lfs_available():
            raise DatasetError("git lfs is not installed: install it from https://git-lfs.com and run remote add again")
        name = github_name(url)
        storage = "lfs" if lfs else "s3"
        keys = None
        if storage == "s3" and storage_url is None:
            if not name:
                raise DatasetError(f"cannot read the repository name from {url}")
        register(url, credential)
        if storage == "s3" and storage_url is None:
            keys = self._keys(repository=name, level="read_write")

        self.git.run("remote", "add", "origin", url)
        if lfs:
            self.git.lfs("install", "--local")
            self._write_attributes()
            where = f"the GitHub LFS of {name or url}"
            added = [footprint.ATTRIBUTES_FILE]
        else:
            endpoint = region = None
            if keys is not None:
                storage_url, endpoint, region = f"s3://{keys.prefix}", keys.endpoint, keys.region
            self.dvc.init()
            self.dvc.config("core.analytics", "false", local=False)
            self.dvc.set_remote(storage_url, endpoint=endpoint, region=region)
            where = storage_url
            added = [".dvc", ".dvcignore"]
            self._ignore(".dvcignore")
        with (self.path / footprint.CONFIG_FILE).open("a") as config:
            config.write(f"storage: {storage}\n")
        self.git.add([footprint.CONFIG_FILE, *added])
        self.warnings = self._access_warnings(url, credential, check_access)
        return where

    def add_credential(self, credential: Optional[str]) -> Dict[str, Any]:
        """Set the credential the server reads this dataset with: the origin's repository, by its owner or an admin. An
        empty credential makes the dataset public. Needs remote add first."""
        self._require_setup()
        url = self.git.remote_url()
        if not url:
            raise DatasetError("no remote is configured: run 'cairnml dataset remote add <url>' first")
        return set_dataset_credential(url, credential)

    @staticmethod
    def _access_warnings(url: str, credential: Optional[str],
                         check_access: Optional[Callable[[str, str], Optional[str]]]) -> List[str]:
        """What the person must do about access, as warnings: nothing fails, the set-up is kept. Without a credential the
        dataset is public, unless the repository is private, which the credential is for. With one, the repository must be
        reachable with the person's login."""
        if not credential:
            return [
                f"no credential was given for {url}. If the repository is not public, it cannot be read until a credential "
                "is added to the dataset: cairnml dataset add-credential <credential>, or in the web app under Datasets. "
                "List the credentials you can use with cairnml credential list."
            ]
        try:
            (check_access or fetch_person_git_token)(url, "read")
        except DatasetError as exc:
            return [
                f"{url} is not reachable with the credential {credential}: {exc}. Check that the credential can read the "
                "repository (a GitHub App must be installed on it), or choose another one with cairnml dataset add-credential."
            ]
        return []

    def _ignore(self, name: str) -> None:
        """Add the IGNORED patterns to an ignore file of the repository, keeping its other lines."""
        target = self.path / name
        existing = target.read_text() if target.is_file() else ""
        missing = [pattern for pattern in IGNORED if pattern not in existing.splitlines()]
        if missing:
            separator = "" if not existing or existing.endswith("\n") else "\n"
            target.write_text(existing + separator + "".join(pattern + "\n" for pattern in missing))

    def _write_attributes(self) -> None:
        """Keep every file of data/ in LFS: the pattern is added to .gitattributes, which is tracked."""
        target = self.path / footprint.ATTRIBUTES_FILE
        existing = target.read_text() if target.is_file() else ""
        if footprint.LFS_PATTERN not in existing.splitlines():
            target.write_text(existing + ("" if not existing or existing.endswith("\n") else "\n") + footprint.LFS_PATTERN + "\n")

    def _write_tooling(self) -> None:
        """The footprint script and the workflow that runs it, kept at this tool's version."""
        (self.path / ".cairnml").mkdir(exist_ok=True)
        shutil.copyfile(footprint.__file__, self.path / footprint.VERIFY_SCRIPT)
        workflow = self.path / WORKFLOW_FILE
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text(WORKFLOW)

    # status

    def status(self) -> Dict[str, object]:
        """The branch, and the data changes in three groups, like git: not added, added, and untracked."""
        self._require_setup()
        dvc = self._changes()

        def files(group: Dict[str, List[str]]) -> Dict[str, List[str]]:
            return {
                kind: [p for p in group.get(kind, []) if not p.endswith("/")] for kind in ("added", "modified", "deleted")
            }

        ours = (DVC_FILE, ".gitignore", footprint.CONFIG_FILE, footprint.LOCK_FILE)
        other = [
            path for _i, _w, path in self.git.porcelain()
            if path not in ours and not path.startswith((".dvc", ".cairnml", ".github"))
        ]
        return {
            "branch": self.git.branch(),
            "commit": self.git.head(),
            "untracked": [p for p in dvc.get("untracked", []) if p.startswith(f"{DATA_DIR}/")],
            "not_added": files(dvc.get("uncommitted", {})),
            "added": files(dvc.get("committed", {})),
            "other": [p for p in other if not p.startswith(f"{DATA_DIR}/")],
        }

    def _changes(self) -> Dict[str, Any]:
        """The state of ``data/``, in the shape of the DVC status: from DVC for s3, from git for lfs."""
        if self.storage() == "s3":
            return self.dvc.status()
        changes: Dict[str, Any] = {"untracked": [], "uncommitted": {}, "committed": {}}
        kinds = {"A": "added", "C": "added", "M": "modified", "R": "modified", "D": "deleted"}
        for group in ("uncommitted", "committed"):
            changes[group] = {kind: [] for kind in ("added", "modified", "deleted")}
        for index, worktree, path in self.git.porcelain():
            if not path.startswith(DATA_DIR + "/"):
                continue
            if index == "?":
                changes["untracked"].append(path)
                continue
            for column, group in ((index, "committed"), (worktree, "uncommitted")):
                if column in kinds:
                    changes[group][kinds[column]].append(path)
        return changes

    # add / check / commit

    def add(self) -> Dict[str, object]:
        """Record the current state of ``data/``: new and changed files are included, deleted files are removed."""
        self._require_setup()
        if not (self.path / DATA_DIR).is_dir():
            raise DatasetError(f"there is no {DATA_DIR}/ folder: copy your files into {DATA_DIR}/ first")
        if self._require_remote() == "lfs":
            self._add_lfs()
        else:
            self.dvc.add(DATA_DIR)
            self.git.add([DVC_FILE, ".gitignore"])
        return self.status()

    def _add_lfs(self) -> None:
        """Stage ``data/`` through the LFS filter. Refused unless every file is stored as an LFS pointer."""
        if not self.git.ok("config", "--get", "filter.lfs.clean"):
            raise DatasetError("git lfs is not set up in this repository: run 'git lfs install --local' and add again")
        self.git.run("add", "-A", "--", DATA_DIR)
        sizes = self.git.staged_sizes(DATA_DIR)
        raw = sorted(path for path, size in sizes.items() if size >= footprint.POINTER_MAX_BYTES)
        if raw:
            raise DatasetError(
                "these files would be stored in git, not in LFS: " + ", ".join(raw[:5])
                + ("" if len(raw) <= 5 else f" and {len(raw) - 5} more")
                + ". Run 'git rm -r --cached data', check that 'git lfs install --local' was run, then add again"
            )

    def check(self) -> Dict[str, Any]:
        """Read ``data/`` in the declared format, validate it, and write the footprint.

        The data must be added first, so ``data.dvc`` describes what is in ``data/``. The footprint, the script that
        verifies it and the workflow are staged, ready to be committed.
        """
        self._require_setup()
        storage = self._require_storage()
        dvc = self._changes()
        pending = [p for p in dvc.get("untracked", []) if p.startswith(f"{DATA_DIR}/")]
        pending += [p for kind in ("added", "modified", "deleted") for p in dvc.get("uncommitted", {}).get(kind, [])]
        if pending:
            raise DatasetError("data/ has changes that are not added; run 'cairnml dataset add' first")
        dvc_file = self.path / DVC_FILE
        config_file = self.path / footprint.CONFIG_FILE
        if storage == "s3" and not dvc_file.exists():
            raise DatasetError(f"{DVC_FILE} is missing; run 'cairnml dataset add' first")
        if not config_file.exists():
            raise DatasetError(f"{footprint.CONFIG_FILE} is missing; say how data/ is read ('format: native' or 'adapter: <name>')")
        try:
            config = footprint.parse_config(config_file.read_text())
            dvc_info = footprint.parse_dvc_file(dvc_file.read_text()) if storage == "s3" else None
        except ValueError as exc:
            raise DatasetError(str(exc)) from None

        result = run_check(self.path / DATA_DIR, config, self._cache)
        lfs = footprint.lfs_record(str(self.path)) if storage == "lfs" else None
        lock = footprint.build_lock(
            dvc_info, result["format"], result["samples"], result["modalities"], result["labels"], __version__, lfs=lfs
        )
        (self.path / footprint.LOCK_FILE).write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
        manifest = {
            "schema_version": 1,
            "modalities": result["modalities"],
            "labels": result["labels"],
            "samples": result["samples"],
            "splits": result["splits"],
        }
        (self.path / footprint.MANIFEST_FILE).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        self._write_tooling()
        added = [footprint.LOCK_FILE, footprint.MANIFEST_FILE, footprint.CONFIG_FILE, ".cairnml", ".github"]
        if storage == "lfs":
            added.append(footprint.ATTRIBUTES_FILE)
        if result["format"]["kind"] == "local":
            added.append(footprint.ADAPTER_FILE)  # the lock records its hash: the commit must hold this exact file
        self.git.add(added)
        return lock

    def commit(self, message: str) -> str:
        """Check the data, then commit it with its footprint."""
        self._require_setup()
        self._require_remote()
        self.check()
        if not self.git.has_staged():
            raise DatasetError("nothing to commit: run add first")
        return self.git.commit(message)

    # push

    def problems(self) -> List[str]:
        """What stops this repository from being published, as sentences. Empty when the footprint holds."""
        files = footprint.read_files(str(self.path))
        lfs = None
        if footprint.uses_lfs(files):
            try:
                lfs = footprint.lfs_record(str(self.path))
            except ValueError as exc:
                return [str(exc)]
        problems = footprint.verify_files(files, self.git.tracked_sizes(), lfs)
        dirty = self.git.run(
            "status", "--porcelain", "--", DVC_FILE, footprint.CONFIG_FILE, footprint.LOCK_FILE, footprint.ADAPTER_FILE
        )
        if dirty:
            problems.append(
                "data.dvc, dataset.yaml, dataset.lock.json or adapter.py has uncommitted changes; run 'cairnml dataset commit'"
            )
        changes = self._changes()
        if lfs is not None and (changes["untracked"] or any(changes["uncommitted"].values())):
            problems.append("data/ has changes that are not committed; run 'cairnml dataset commit'")
        return problems

    def push(self) -> Dict[str, Optional[str]]:
        """Upload the files, then publish the commit. A commit is never published before its files.

        The default branch of a dataset repository takes pull requests only, so a commit made there goes to a branch
        ``incoming/<commit>`` and a pull request is opened if the GitHub command line is installed. The first push to
        an empty repository goes straight to its first branch.
        """
        self._require_setup()
        storage = self._require_remote()
        head = self.git.head()
        if not head:
            raise DatasetError("nothing to push: commit first")
        problems = self.problems()
        if problems:
            raise DatasetError("this dataset does not pass its footprint check:\n  - " + "\n  - ".join(problems))
        pusher = self._push_git()
        if storage == "lfs":
            pusher.lfs("push", "origin", head, transfer=True)
        else:
            self._apply_keys()
            self.dvc.push()

        branch = self.git.branch()
        heads = self.git.remote_heads()
        default = self.git.default_branch() if heads else branch
        if heads and branch == default:
            target = f"incoming/{head[:10]}"
            pusher.push_to(target)
            return {"commit": head, "branch": target, **self._open_pull_request(target, default)}
        pusher.push_to(branch)
        return {"commit": head, "branch": branch, "pull_request": None, "problem": None, "auto_merge": None}

    def _push_git(self) -> Git:
        """The git that publishes the commit and its LFS objects to origin. On GitHub the token is the server's, at
        read_write, for this repository only: the server gives it for the level the person holds on the dataset, and
        refuses otherwise. A public repository or another host has no such token, and the person's own git login is
        used."""
        origin = self.git.remote_url() or ""
        token = git_token(origin, "read_write") if on_github(origin) else None
        return Git(self.path, token=token)

    def _open_pull_request(self, branch: str, base: str) -> Dict[str, Any]:
        """Open a pull request and ask for auto-merge, through the GitHub command line.

        Returns ``pull_request`` (its URL, or None), ``auto_merge`` (whether it was switched on, or None when there
        is no pull request) and ``problem`` (why a step did not work, in the tool's own words, or None).
        """
        none = {"pull_request": None, "auto_merge": None}
        if shutil.which("gh") is None:
            return {**none, "problem": "the GitHub command line (gh) is not installed"}
        try:
            made = process.run(
                ["gh", "pr", "create", "--base", base, "--head", branch, "--fill"], cwd=str(self.path)
            )
            if made.returncode != 0:
                return {**none, "problem": (made.stderr or made.stdout).strip() or "gh pr create failed"}
            url = (made.stdout.strip().splitlines() or [None])[-1]
            merge = process.run(["gh", "pr", "merge", "--auto", "--merge", branch], cwd=str(self.path))
        except process.CommandTimeout as exc:
            return {**none, "problem": str(exc)}
        if merge.returncode != 0:
            return {
                "pull_request": url, "auto_merge": False,
                "problem": (merge.stderr or merge.stdout).strip() or "gh pr merge failed",
            }
        return {"pull_request": url, "auto_merge": True, "problem": None}

    def compare_url(self, branch: str) -> Optional[str]:
        name = github_name(self.git.remote_url() or "")
        return f"https://github.com/{name}/compare/{self.git.default_branch()}...{branch}?expand=1" if name else None

    # doctor

    def doctor(self, list_datasets: Optional[Callable[[], Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """Check what publishing needs, one entry per check: the set-up here and the GitHub settings that the footprint
        check relies on. ``ok`` is True, False (a problem, with what to do) or None (nothing to check yet).

        ``list_datasets`` answers the server's dataset list (see credentials.fetch_dataset_list); the App check uses it.
        It reads the settings only; it does not change them.
        """
        checks: List[Dict[str, Any]] = []

        def check(name: str, ok: Optional[bool], message: str) -> None:
            checks.append({"check": name, "ok": ok, "message": message})

        if not self.git.is_repo():
            check("repository", False, "not a git repository: run cairnml dataset init")
            return checks
        repo = github_name(self.git.remote_url() or "")
        if not repo:
            check("origin", False, "no GitHub origin: run cairnml dataset remote add <url>")
            return checks
        check("origin", True, f"origin is {repo}")
        ok, message = self._app_installed(repo, list_datasets or fetch_dataset_list)
        check("App installed", ok, message)

        storage = self.storage()
        setup = storage is not None and ((self.path / ".dvc").is_dir() or storage == "lfs")
        if setup:
            check("set-up", True, "dataset storage and dataset.yaml are in place")
        elif (self.path / footprint.CONFIG_FILE).is_file():
            check("set-up", False, "no storage is chosen yet: run cairnml dataset remote add <url>")
        else:
            check("set-up", False, "run: cairnml dataset init")
        if setup and self.storage() == "lfs":
            lfs = self.git.lfs_available()
            check("git lfs", lfs, "git lfs is installed" if lfs
                  else "git lfs is not installed: install it from https://git-lfs.com")
        workflow = self.path / WORKFLOW_FILE
        current = workflow.is_file() and workflow.read_text() == WORKFLOW
        check("workflow", current, f"{WORKFLOW_FILE} is in place" if current
              else "missing or out of date: run cairnml dataset check, then commit the change")
        script = self.path / footprint.VERIFY_SCRIPT
        current = script.is_file() and script.read_bytes() == Path(footprint.__file__).read_bytes()
        check("verify script", current, f"{footprint.VERIFY_SCRIPT} matches this version" if current
              else "missing or from another version: run cairnml dataset check, then commit the change")

        if not self.git.remote_heads():
            check("default branch", None, "the repository is empty: the first push creates its default branch, "
                  "protect it after that and run doctor again")
            return checks
        default = self.git.default_branch()
        if shutil.which("gh") is None:
            check("GitHub command line", False, "gh is not installed: push prints the link to open the pull request "
                  "instead. Install it from https://cli.github.com to let push open it and switch on auto-merge")
            check("branch protection", None, f"cannot be read without gh: check {default} at "
                  f"https://github.com/{repo}/settings/branches")
            return checks
        check("GitHub command line", True, "gh is installed")
        try:
            login = process.run(["gh", "auth", "status"], cwd=str(self.path))
        except process.CommandTimeout as exc:
            check("GitHub login", None, str(exc))
            return checks
        if login.returncode != 0:
            check("GitHub login", False, "gh is not logged in: run gh auth login")
            return checks
        check("GitHub login", True, "gh is logged in")

        settings, why = self._gh_api(f"repos/{repo}")
        if settings is None:
            check("auto-merge", None, f"could not read the repository settings: {why}")
        elif settings.get("allow_auto_merge"):
            check("auto-merge", True, "auto-merge is allowed, so push can switch it on")
        else:
            check("auto-merge", False, f"auto-merge is off: switch on 'Allow auto-merge' at "
                  f"https://github.com/{repo}/settings")

        protection, why = self._gh_api(f"repos/{repo}/branches/{default}/protection")
        if protection is None and "Branch not protected" in why:
            check("branch protection", False, f"{default} is not protected. At https://github.com/{repo}/settings/"
                  f"branches require pull requests, block force pushes and deletions. "
                  "cairnml dataset create sets this up (it needs administration rights)")
        elif protection is None:
            check("branch protection", None, f"could not read the protection of {default}: {why}")
        else:
            problems = protection_problems(protection)
            if problems:
                check("branch protection", False, f"{default}: " + "; ".join(problems) + f" (see https://github.com/"
                      f"{repo}/settings/branches)")
            else:
                check("branch protection", True, f"{default} requires pull requests and blocks force pushes and deletions")
        return checks

    def _app_installed(self, repo: str, list_datasets: Callable[[], Dict[str, Any]]) -> Tuple[Optional[bool], str]:
        """Whether the server lists the repository as a dataset this user can read (datasets.visible): its state is ok.
        A listed repository the server cannot read says why. The server's records are read, not changed."""
        try:
            answer = list_datasets()
        except DatasetError:
            return None, "the server is not reachable or not logged in; nothing checked"
        rows = [item for item in answer.get("datasets") or []
                if item.get("kind") == "repo" and str(item.get("ref", "")).lower() == repo.lower()]
        if any(item.get("state") == "ok" for item in rows):
            return True, f"the server lists {repo} as a dataset you can read"
        if rows:
            return False, f"the server lists {repo} but cannot read it: {rows[0].get('state_message') or 'no access'}"
        return False, (f"the server does not list {repo}: the GitHub App may not be installed on it, or the repository "
                       "has no dataset.yaml yet (see the dataset docs)")

    def _gh_api(self, path: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """The JSON answer of one GitHub API call through gh, or None and why it failed."""
        try:
            result = process.run(["gh", "api", path], cwd=str(self.path))
        except process.CommandTimeout as exc:
            return None, str(exc)
        if result.returncode != 0:
            return None, (result.stderr or result.stdout).strip() or f"gh api {path} failed"
        return json.loads(result.stdout or "{}"), ""

    # protect

    def protect(self) -> Dict[str, Any]:
        """Protect the default branch of a new repository, through gh. Needs administration rights on the repository.

        Pull requests are required, force pushes and deletions are blocked, and no status check is required.
        Returns ``ok`` and ``problem`` (why the protection was not set, or None). It never raises: create reports
        the problem and carries on.
        """
        name = github_name(self.git.remote_url() or "")
        if not name:
            return {"ok": False, "problem": "origin is not a GitHub repository, so there is no branch to protect"}
        if shutil.which("gh") is None:
            return {"ok": False, "problem": "the GitHub command line (gh) is not installed"}
        try:
            made = process.run(
                ["gh", "api", "-X", "PUT", f"repos/{name}/branches/{DEFAULT_BRANCH}/protection", "--input", "-"],
                cwd=str(self.path), stdin=json.dumps(protection_body()),
            )
        except process.CommandTimeout as exc:
            return {"ok": False, "problem": str(exc)}
        if made.returncode != 0:
            text = (made.stderr or made.stdout).strip() or "gh api failed"
            if "403" in text:
                text = (f"needs administration rights on {name} (the Administration permission for a token, or the "
                        f"admin role for a login); gh said: {text}")
            return {"ok": False, "problem": text}
        return {"ok": True, "problem": None}

    def _apply_keys(self) -> None:
        url = self.dvc.remote_url()
        if url and url.startswith("s3://"):
            origin = self.git.remote_url() or ""
            name = github_name(origin)
            if not name:
                raise DatasetError(f"cannot read the repository name from {origin}")
            keys = self._keys(repository=name, level="read_write")
            self.dvc.set_credentials(keys.access_key, keys.secret_key, keys.session_token)

    def _require_setup(self) -> None:
        if not ((self.path / ".dvc").is_dir() or (self.path / footprint.CONFIG_FILE).is_file()):
            raise DatasetError("this repository is not set up for datasets: run init first")

    # create

    @staticmethod
    def create(
        path: Union[str, Path],
        name: str,
        source: Union[str, Path],
        *,
        private: bool = True,
        storage: str = "s3",
        adapter: Optional[str] = None,
        registry: Optional[str] = None,
        create_remote: Callable[[str, bool], str] = create_repository,
        protect: bool = True,
        credential: Optional[str] = None,
        register: Callable[[str, Optional[str]], dict] = register_dataset,
        check_access: Optional[Callable[[str, str], Optional[str]]] = None,
    ) -> Dict[str, Any]:
        """Create the GitHub repository, then set it up as init and remote add do, copy the files of ``source`` into
        ``data/``, check, commit and push. The one call a new dataset needs; the steps are the same as the separate
        commands, which is what to use for a repository that already exists.

        ``create_remote`` makes the repository and returns its address for git. After the first push the default branch
        is protected (see :meth:`protect`), unless ``protect`` is False; a failure there is reported in ``protection`` and
        does not undo the create.
        """
        if storage not in footprint.STORAGES:
            raise DatasetError(f"storage must be s3 or lfs, not {storage!r}")
        source_dir, target = Path(source), Path(path)
        if not source_dir.is_dir():
            raise DatasetError(f"{source} is not a folder: give the folder that holds the dataset files")
        if target.exists() and any(target.iterdir()):
            raise DatasetError(f"{target} exists and is not empty: choose another --path")
        local = adapter == footprint.LOCAL_ADAPTER
        if local and not (source_dir / footprint.ADAPTER_FILE).is_file():
            raise DatasetError(
                f"{source} has no {footprint.ADAPTER_FILE}: with adapter: {footprint.LOCAL_ADAPTER} the adapter is that file, "
                f"which goes to the top of the repository and not into data/"
            )
        url = create_remote(name, private)
        repo = DatasetRepo(target)
        existed = target.exists()
        try:
            repo.init(adapter=adapter, registry=registry)
            where = repo.remote_add(url, lfs=storage == "lfs", credential=credential, register=register,
                                    check_access=check_access)
        except DatasetError as exc:
            if not existed:
                shutil.rmtree(target, ignore_errors=True)  # the folder this create made: nothing is left behind
            raise DatasetError(
                f"{url} was created on GitHub, but the set-up stopped before anything was pushed: {exc}"
            ) from None

        def skip_top_adapter(folder, names):
            # the adapter is taken out of the top folder only: a file of the same name below it is data
            return [footprint.ADAPTER_FILE] if Path(folder) == source_dir else []

        if local:
            shutil.copyfile(source_dir / footprint.ADAPTER_FILE, target / footprint.ADAPTER_FILE)
        shutil.copytree(source_dir, target / DATA_DIR, dirs_exist_ok=True, ignore=skip_top_adapter if local else None)
        repo.add()
        commit = repo.commit("Add dataset")
        pushed = repo.push()
        protection = repo.protect() if protect else None
        return {
            "url": url, "storage": storage, "where": where, "commit": commit, "branch": pushed["branch"],
            "protection": protection, "warnings": repo.warnings,
        }

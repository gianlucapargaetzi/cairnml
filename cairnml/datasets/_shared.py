"""What local and external datasets both need: git access, the repository mirror, and the record on the task."""

import os
import re
from typing import Dict, Optional

from .cache import DatasetCache
from .credentials import fetch_git_token
from .errors import DatasetError
from .gitutil import Git

SECTION = "Datasets"


def git_token(repo: str) -> Optional[str]:
    """A read-only GitHub token for a job; None on a person's machine, where their own git login is used."""
    return fetch_git_token(repo, os.environ.get("CAIRNML_TASK_ID", ""))


def mirror(cache: DatasetCache, repo: str, token: Optional[str]) -> Git:
    """The bare copy of a repository kept in the cache (metadata only)."""
    directory = cache.repo_dir(repo)
    if directory.exists():
        return Git(directory, token=token, bare=True)
    return Git.clone_bare(repo, directory, token=token)


def resolve_commit(git: Git, repo: str, commit: str) -> str:
    """The full sha of a commit, branch or tag, fetching once if it is not known yet."""
    if not git.has_commit(commit):
        git.fetch_all()
    if not git.has_commit(commit):
        raise DatasetError(f"commit {commit} is not on {repo}: push it first")
    return git.resolve(commit)


def default_alias(repo_or_name: str) -> str:
    return re.sub(r"\.git$", "", repo_or_name.rstrip("/")).rsplit("/", 1)[-1]


def record(alias: str, values: Dict[str, str]) -> None:
    """Write the dataset onto the current task, if there is one."""
    from .. import Task

    task = Task.current_task()
    if task is None:
        return
    # set_parameters replaces the whole parameter set; set_parameter adds one.
    for key, value in values.items():
        task.set_parameter(f"{SECTION}/{alias}/{key}", value)

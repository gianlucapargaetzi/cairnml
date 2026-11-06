"""``cairnml dataset clone``: a working copy of a dataset on this machine.

The server says whether the signed-in person (or the job) may see the dataset, and gives the read token of its credential
(none for a public repository). The repository is cloned with that token for one command, the commit is checked out, and
the data is copied from the dataset cache into ``data/``, which git does not track (the repository ignores it).

Once access ends, the copy stays readable here; the server refuses its changes (a push needs a write token from it).
"""

import os
import shutil
from pathlib import Path
from typing import Optional

from .credentials import _person_request
from .dataset import Dataset
from .errors import DatasetError
from .gitutil import _env
from . import process


def _clone_answer(reference: str, commit: Optional[str], session=None) -> dict:
    task = os.environ.get("CAIRNML_TASK_ID") or None
    body = {"ref": reference}
    if commit:
        body["commit"] = commit
    if task:
        body["task"] = task
    response = _person_request(session, "clone", body)
    if not response.ok:
        raise DatasetError(f"the server could not clone {reference}: {response.text.strip()}")
    return (response.json().get("data") or {})


def clone_dataset(reference: str, path: Optional[str] = None, commit: Optional[str] = None, session=None) -> Path:
    """Clones the dataset ``reference`` names into ``path`` (default: its name, in this folder) and returns the folder."""
    answer = _clone_answer(reference, commit, session)
    repository = answer.get("repository")
    if not repository:
        raise DatasetError(f"the server did not say where {reference} is")
    name = (answer.get("dataset") or {}).get("name") or reference.rstrip("/").rsplit("/", 1)[-1]
    target = Path(path or name)
    if target.exists() and any(target.iterdir()):
        raise DatasetError(f"{target} exists and is not empty: choose another folder")
    target.parent.mkdir(parents=True, exist_ok=True)
    token = answer.get("token")
    try:
        result = process.run(["git", "clone", "-q", repository, str(target)], env=_env(token),
                             timeout=process.transfer_timeout())
    except FileNotFoundError:
        raise DatasetError("git is not installed") from None
    if result.returncode != 0:
        raise DatasetError(f"could not clone {repository}: {result.stderr.strip()}")
    head = answer.get("commit")
    if head:
        checkout = process.run(["git", "-C", str(target), "checkout", "-q", head], env=_env(None))
        if checkout.returncode != 0:
            raise DatasetError(f"{head} is not in {repository}: {checkout.stderr.strip()}")
    dataset = Dataset.get(reference, head or "HEAD", record=False)
    shutil.copytree(dataset.path, target / "data", dirs_exist_ok=True)
    return target

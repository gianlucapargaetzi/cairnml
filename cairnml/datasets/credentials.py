"""Keys and tokens the server hands out for datasets, so no one copies an access key to a machine."""

import os
from dataclasses import dataclass, field
from typing import Optional

from .errors import DatasetError


@dataclass
class StorageKeys:
    endpoint: str
    region: str
    bucket: str
    prefix: str
    """ the bucket path of the dataset, bucket name first (dataset/<company>/<owner>/<name>): the remote is s3:// + this """
    access_key: str
    secret_key: str = field(repr=False)
    session_token: str = field(repr=False)
    expires_at: str


def _session(session=None):
    if session is not None:
        return session
    from .. import Task

    return Task._get_default_session()


def fetch_storage_keys(repository: str, level: str, session=None) -> StorageKeys:
    """Short-lived keys for one dataset's prefix in the bucket, at ``level`` (``read`` or ``read_write``). They work
    for DVC on that prefix only, and expire on their own. ``repository`` is owner/name on GitHub."""
    response = _session(session).send_request(
        service="datachunks",
        action="dataset_credentials",
        version="2.40",
        json={"repository": repository, "level": level},
    )
    if not response.ok:
        raise DatasetError(f"the server refused the storage keys for datasets: {response.text.strip()}")
    data = response.json().get("data") or {}
    try:
        return StorageKeys(**{name: data[name] for name in StorageKeys.__dataclass_fields__})
    except KeyError as exc:
        raise DatasetError(f"the server's answer for the storage keys is missing {exc}") from None


def running_as_job() -> bool:
    """True inside a job started by an agent, where git access comes from the server, not from the user."""
    return bool(os.environ.get("CAIRNML_JOB_TOKEN") or os.environ.get("CAIRNML_JOB_TOKEN_FILE"))


def fetch_git_token(repository: str, task_id: str, session=None) -> Optional[str]:
    """A read-only GitHub token for one dataset repository, for a job. None when this is not a job."""
    if not running_as_job():
        return None
    response = _session(session).send_request(
        service="devices", action="git_token", json={"task": task_id, "repository": repository}
    )
    if not response.ok:
        raise DatasetError(f"the server refused a token for {repository}: {response.text.strip()}")
    return (response.json().get("data") or {}).get("token")


def _person_request(session, action: str, json: dict):
    """A datasets request made for a person, with the login. Without a login the reason is said in the error."""
    try:
        return _session(session).send_request(service="datasets", action=action, version="2.40", json=json)
    except Exception as exc:
        raise DatasetError(
            f"the server could not be asked ({exc}): datasets need a CairnML login, because the server issues the Git "
            "tokens of a dataset to the person signed in"
        ) from None


def fetch_person_git_token(repository: str, level: str, session=None) -> Optional[str]:
    """A GitHub token for one dataset repository, for a person's machine, at ``level`` (``read`` or ``read_write``), from
    the server (datasets.git_token). The server gives the level the person holds on the dataset, and refuses the rest.
    None for a public repository, which needs no token to read. ``repository`` is the repository URL."""
    response = _person_request(session, "git_token", {"repository": repository, "level": level})
    if not response.ok:
        raise DatasetError(f"the server refused a token for {repository}: {response.text.strip()}")
    return (response.json().get("data") or {}).get("token")


def register_dataset(url: str, session=None) -> None:
    """Adds a new GitHub repository to CairnML as a dataset of the person signed in (datasets.add: kind repo, visibility
    user), so that the owner can be given the token that pushes to it. Raises DatasetError with the server's reason when
    the server refuses."""
    response = _person_request(session, "add", {"kind": "repo", "url": url, "visibility": "user"})
    if not response.ok:
        raise DatasetError(f"the server did not add {url} as a dataset: {response.text.strip()}")


def fetch_dataset_list(session=None) -> dict:
    """The datasets the signed-in user may see (datasets.visible): ``datasets``, each with its ``ref`` (owner/name), its
    ``kind`` and its ``state`` (ok, or no_access with a message). The server answers from its own records, so this does
    not reach GitHub. Raises DatasetError when the server cannot be asked, for instance when there is no CairnML login."""
    try:
        response = _session(session).send_request(service="datasets", action="visible", version="2.40", json={})
        if not response.ok:
            raise DatasetError(f"the server refused the dataset list: {response.text.strip()}")
        return response.json().get("data") or {}
    except DatasetError:
        raise
    except Exception as exc:  # no login, no network, a malformed answer: all mean the server cannot answer
        raise DatasetError(f"the server could not be asked for the dataset list ({exc})") from None

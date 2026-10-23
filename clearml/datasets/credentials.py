"""Keys and tokens the server hands out for datasets, so no one copies an access key to a machine."""

import os
from dataclasses import dataclass
from typing import Optional

from .errors import DatasetError


@dataclass
class StorageKeys:
    endpoint: str
    region: str
    bucket: str
    access_key: str
    secret_key: str
    session_token: str
    expires_at: str


def _session(session=None):
    if session is not None:
        return session
    from .. import Task

    return Task._get_default_session()


def fetch_storage_keys(session=None) -> StorageKeys:
    """Short-lived keys for the dataset bucket. They work for DVC and expire on their own."""
    response = _session(session).send_request(service="datachunks", action="dataset_credentials", json={})
    if not response.ok:
        raise DatasetError(f"the server refused the storage keys for datasets: {response.text.strip()}")
    data = response.json().get("data") or {}
    try:
        return StorageKeys(**{name: data[name] for name in StorageKeys.__dataclass_fields__})
    except KeyError as exc:
        raise DatasetError(f"the server's answer for the storage keys is missing {exc}") from None


def running_as_job() -> bool:
    """True inside a job started by an agent, where git access comes from the server, not from the user."""
    return bool(os.environ.get("CLEARML_JOB_TOKEN") or os.environ.get("CLEARML_JOB_TOKEN_FILE"))


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


def _server_message(response) -> str:
    try:
        meta = response.json().get("meta") or {}
        return meta.get("result_msg") or response.text.strip()
    except ValueError:
        return response.text.strip()


def register_dataset(repository: str, name: Optional[str] = None, session=None) -> dict:
    """Ask the server to add a dataset repository to the dataset registry. It checks the footprint itself."""
    body = {"repository": repository}
    if name:
        body["name"] = name
    response = _session(session).send_request(service="dataset_registry", action="register", json=body)
    if not response.ok:
        raise DatasetError(f"the server did not register {repository}: {_server_message(response)}")
    return response.json().get("data") or {}

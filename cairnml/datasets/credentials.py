"""Keys and tokens the server hands out for datasets, so no one copies an access key to a machine."""

import os
from dataclasses import dataclass, field
import re
from typing import Optional

from .errors import DatasetError
from ..backend_api.session.defs import MissingConfigError, newest_version
from ..backend_api.session.session import NotSignedInError
from ..backend_config.errors import ConfigurationError


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


def _reason(response) -> str:
    """Why the server refused: its message, without the envelope and the "Validation error (...)" wrapper. The body when it has
    no message."""
    try:
        message = ((response.json() or {}).get("meta") or {}).get("result_msg") or ""
    except ValueError:
        message = ""
    if not message:
        return (response.text or "").strip()
    wrapped = re.fullmatch(r"Validation error \((.*)\)", message, re.S)
    return wrapped.group(1) if wrapped else message


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
        version=newest_version("datachunks", "dataset_credentials"),
        json={"repository": repository, "level": level},
    )
    if not response.ok:
        raise DatasetError(f"the server refused the storage keys for datasets: {_reason(response)}")
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
        raise DatasetError(f"the server refused a token for {repository}: {_reason(response)}")
    return (response.json().get("data") or {}).get("token")


LOGIN_HINT_FOR_TOKENS = (
    "datasets need a CairnML login, because the server issues the Git tokens of a dataset to the person signed in"
)
LOGIN_HINT_FOR_LIST = "datasets need a CairnML login"


def _not_signed_in(exc: Exception) -> bool:
    """True when the failure means the person is not signed in, or the machine is not set up to sign in. A connection
    error or a bug in the transport is not one of them."""
    return isinstance(exc, (NotSignedInError, MissingConfigError, ConfigurationError))


def _could_not_ask(exc: Exception, what: str, login_hint: str) -> str:
    """The message for a request that failed. The login hint is added only when the person is not signed in."""
    message = f"the server could not be asked{what} ({exc})"
    return f"{message}: {login_hint}" if _not_signed_in(exc) else message


def _person_request(session, action: str, json: dict, service: str = "repositories"):
    """A request made for a person, with the login. Without a login the reason is said in the error."""
    try:
        return _session(session).send_request(
            service=service, action=action, version=newest_version(service, action), json=json
        )
    except Exception as exc:
        raise DatasetError(_could_not_ask(exc, "", LOGIN_HINT_FOR_TOKENS)) from exc


def fetch_person_git_token(repository: str, level: str, session=None) -> Optional[str]:
    """A token for one repository, for a person's machine, at ``level`` (``read`` or ``read_write``), from the server
    (repositories.git_token). The server gives read to anyone who sees the repository and write to its owner. None for a
    repository the host clones without a credential. ``repository`` is the repository URL."""
    response = _person_request(session, "git_token", {"ref": repository, "level": level}, service="repositories")
    if not response.ok:
        raise DatasetError(f"the server refused a token for {repository}: {_reason(response)}")
    return (response.json().get("data") or {}).get("token")


def _row(response, what: str) -> dict:
    if not response.ok:
        raise DatasetError(f"the server refused {what}: {_reason(response)}")
    return (response.json().get("data") or {}).get("repository") or {}


def add_repository(
    url: str,
    role: str = "dataset",
    credential: Optional[str] = None,
    none_needed: bool = False,
    shared: bool = False,
    project: Optional[str] = None,
    session=None,
) -> dict:
    """Lists a repository (repositories.add) as code, a dataset or a registry. Your entry is non-shared: only you and the
    projects it is listed in see it. ``shared`` (the admin only) makes it visible to everyone. With ``credential`` (one of
    yours, or the company's) or ``none_needed`` your assignment is set too. Returns the repository as the server lists it."""
    body = {"url": url, "role": role}
    for key, value in (
        ("credential", credential),
        ("none_needed", none_needed or None),
        ("shared", shared or None),
        ("project", project),
    ):
        if value:
            body[key] = value
    return _row(_person_request(session, "add", body, service="repositories"), f"to list {url}")


def register_dataset(url: str, credential: Optional[str] = None, session=None) -> dict:
    """Lists a GitHub repository as a dataset of the person signed in, with their credential when given."""
    return add_repository(url, "dataset", credential, session=session)


def assign_credential(
    repository: str, credential: Optional[str] = None, none_needed: bool = False, role: str = "dataset", session=None
) -> dict:
    """Sets your own assignment on a repository (repositories.assign): one of your credentials, or the company's, or
    ``none_needed`` (the host clones it without one). With neither it is cleared. The credential must read the repository.
    On a shared repository only a company credential is accepted. An address nobody has listed is listed first, with ``role``."""
    body = {"ref": repository, "role": role}
    if credential:
        body["credential"] = credential
    if none_needed:
        body["none_needed"] = True
    return _row(_person_request(session, "assign", body, service="repositories"), f"the credential for {repository}")


def unassign_credential(repository: str, session=None) -> dict:
    """Clears your own assignment on a repository (repositories.unassign)."""
    return _row(
        _person_request(session, "unassign", {"ref": repository}, service="repositories"),
        f"to clear the credential of {repository}",
    )


def check_repository(repository: str, session=None) -> dict:
    """Looks at a repository again (repositories.check) and returns it with what was found."""
    return _row(
        _person_request(session, "check", {"ref": repository}, service="repositories"), f"to check {repository}"
    )


def delete_repository(repository: str, session=None) -> None:
    """Removes a repository you own, with its assignments and links (repositories.delete)."""
    response = _person_request(session, "delete", {"ref": repository}, service="repositories")
    if not response.ok:
        raise DatasetError(f"the server refused to delete {repository}: {_reason(response)}")


def set_repository_shared(
    repository: str, shared: bool, credential: Optional[str] = None, none_needed: bool = False, session=None
) -> dict:
    """Makes a repository shared (the owner or the admin; ownership moves to the admin) or non-shared (the admin). To share, the
    company credential that reads it, or ``none_needed`` for a public repository, is required; the server verifies it first."""
    body = {"ref": repository, "shared": shared}
    if credential:
        body["credential"] = credential
    if none_needed:
        body["none_needed"] = True
    return _row(
        _person_request(session, "set_shared", body, service="repositories"), f"to change the sharing of {repository}"
    )


def link_repository(repository: str, project: str, session=None) -> dict:
    """Lists a repository in a project you are a member of (repositories.link)."""
    return _row(
        _person_request(session, "link", {"ref": repository, "project": project}, service="repositories"),
        f"to list {repository} in the project",
    )


def unlink_repository(repository: str, project: str, session=None) -> dict:
    """Takes a repository out of a project's list (repositories.unlink); the repository and its assignments stay."""
    return _row(
        _person_request(session, "unlink", {"ref": repository, "project": project}, service="repositories"),
        f"to take {repository} out of the project",
    )


def resolve_dataset(reference: str, task: Optional[str] = None, session=None) -> dict:
    """The repository a reference names on the server: its id, its name, or the address of its repository
    (repositories.find). Empty when the server has none that you may see. A job is known to the server by its token."""
    response = _person_request(session, "find", {"ref": reference}, service="repositories")
    if not response.ok:
        raise DatasetError(f"the server could not resolve {reference}: {_reason(response)}")
    return (response.json().get("data") or {}).get("repository") or {}


def list_credentials(session=None) -> list:
    """The credentials the person signed in may use for repositories: their own GitHub Apps and tokens, and the company's.
    Each has an id (what ``cairnml repository assign`` takes), its type, name, scope, owner and state. No secret is returned."""
    response = _person_request(session, "get_all", {}, service="user_access")
    if not response.ok:
        raise DatasetError(f"the server did not list the credentials: {_reason(response)}")
    return (response.json().get("data") or {}).get("credentials") or []


def fetch_repository_list(session=None) -> list:
    """The repositories the signed-in user sees (repositories.get_all): code, datasets and registries. Each has its ``roles``, its
    owner, whether it is ``shared``, the projects it is listed in, every ``assignment`` on it (yours are editable, the others'
    read-only) and ``access``: how you read it now. The server answers from its own records. Raises DatasetError when the server
    cannot be asked, for instance when there is no CairnML login."""
    try:
        response = _session(session).send_request(
            service="repositories", action="get_all", version=newest_version("repositories", "get_all"), json={}
        )
        if not response.ok:
            raise DatasetError(f"the server refused the repository list: {_reason(response)}")
        return (response.json().get("data") or {}).get("repositories") or []
    except DatasetError:
        raise
    except Exception as exc:  # no login, no network, a malformed answer: all mean the server cannot answer
        raise DatasetError(_could_not_ask(exc, " for the repository list", LOGIN_HINT_FOR_LIST)) from exc

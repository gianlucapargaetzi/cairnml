"""Keys and tokens the server hands out for datasets, so no one copies an access key to a machine."""

import os
from dataclasses import dataclass, field
import re
from typing import Optional

from .errors import DatasetError
from ..backend_api.services import datachunks, devices, repositories, user_access
from ..backend_api.session.defs import MissingConfigError
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


def _reason(result) -> str:
    """Why the server refused: its message, without the "Validation error (...)" wrapper."""
    message = (result.meta.result_msg or "").strip()
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
    result = _session(session).send(datachunks.DatasetCredentialsRequest(repository=repository, level=level))
    if not result.ok():
        raise DatasetError(f"the server refused the storage keys for datasets: {_reason(result)}")
    data = result.response_data or {}
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
    result = _session(session).send(devices.GitTokenRequest(task=task_id, repository=repository))
    if not result.ok():
        raise DatasetError(f"the server refused a token for {repository}: {_reason(result)}")
    return (result.response_data or {}).get("token")


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


def _person_request(session, request):
    """A generated request made for a person, with the login. Without a login the reason is said in the error."""
    try:
        return _session(session).send(request)
    except Exception as exc:
        raise DatasetError(_could_not_ask(exc, "", LOGIN_HINT_FOR_TOKENS)) from exc


def fetch_person_git_token(repository: str, level: str, session=None) -> Optional[str]:
    """A token for one repository, for a person's machine, at ``level`` (``read`` or ``read_write``), from the server
    (repositories.git_token). The server gives read to anyone who sees the repository and write to its owner. None for a
    repository the host clones without a credential. ``repository`` is the repository URL."""
    result = _person_request(session, repositories.GitTokenRequest(level=level, ref=repository))
    if not result.ok():
        raise DatasetError(f"the server refused a token for {repository}: {_reason(result)}")
    return (result.response_data or {}).get("token")


def _row(result, what: str) -> dict:
    if not result.ok():
        raise DatasetError(f"the server refused {what}: {_reason(result)}")
    return (result.response_data or {}).get("repository") or {}


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
    request = repositories.AddRequest(
        url=url,
        role=role,
        credential=credential or None,
        none_needed=none_needed or None,
        shared=shared or None,
        project=project or None,
    )
    return _row(_person_request(session, request), f"to list {url}")


def register_dataset(url: str, credential: Optional[str] = None, session=None) -> dict:
    """Lists a GitHub repository as a dataset of the person signed in, with their credential when given."""
    return add_repository(url, "dataset", credential, session=session)


def assign_credential(
    repository: str, credential: Optional[str] = None, none_needed: bool = False, role: str = "dataset", session=None
) -> dict:
    """Sets your own assignment on a repository (repositories.assign): one of your credentials, or the company's, or
    ``none_needed`` (the host clones it without one). With neither it is cleared. The credential must read the repository.
    On a shared repository only a company credential is accepted. An address nobody has listed is listed first, with ``role``."""
    request = repositories.AssignRequest(
        ref=repository, role=role, credential=credential or None, none_needed=True if none_needed else None
    )
    return _row(_person_request(session, request), f"the credential for {repository}")


def unassign_credential(repository: str, session=None) -> dict:
    """Clears your own assignment on a repository (repositories.unassign)."""
    return _row(
        _person_request(session, repositories.UnassignRequest(ref=repository)),
        f"to clear the credential of {repository}",
    )


def check_repository(repository: str, session=None) -> dict:
    """Looks at a repository again (repositories.check) and returns it with what was found."""
    return _row(_person_request(session, repositories.CheckRequest(ref=repository)), f"to check {repository}")


def delete_repository(repository: str, session=None) -> None:
    """Removes a repository you own, with its assignments and links (repositories.delete)."""
    result = _person_request(session, repositories.DeleteRequest(ref=repository))
    if not result.ok():
        raise DatasetError(f"the server refused to delete {repository}: {_reason(result)}")


def set_repository_shared(
    repository: str, shared: bool, credential: Optional[str] = None, none_needed: bool = False, session=None
) -> dict:
    """Makes a repository shared (the owner or the admin; ownership moves to the admin) or non-shared (the admin). To share, the
    company credential that reads it, or ``none_needed`` for a public repository, is required; the server verifies it first."""
    request = repositories.SetSharedRequest(
        ref=repository, shared=shared, credential=credential or None, none_needed=True if none_needed else None
    )
    return _row(_person_request(session, request), f"to change the sharing of {repository}")


def link_repository(repository: str, project: str, session=None) -> dict:
    """Lists a repository in a project you are a member of (repositories.link)."""
    return _row(
        _person_request(session, repositories.LinkRequest(ref=repository, project=project)),
        f"to list {repository} in the project",
    )


def unlink_repository(repository: str, project: str, session=None) -> dict:
    """Takes a repository out of a project's list (repositories.unlink); the repository and its assignments stay."""
    return _row(
        _person_request(session, repositories.UnlinkRequest(ref=repository, project=project)),
        f"to take {repository} out of the project",
    )


def resolve_dataset(reference: str, task: Optional[str] = None, session=None) -> dict:
    """The repository a reference names on the server: its id, its name, or the address of its repository
    (repositories.find). Empty when the server has none that you may see. A job is known to the server by its token."""
    result = _person_request(session, repositories.FindRequest(ref=reference))
    if not result.ok():
        raise DatasetError(f"the server could not resolve {reference}: {_reason(result)}")
    return (result.response_data or {}).get("repository") or {}


def list_credentials(session=None) -> list:
    """The credentials the person signed in may use for repositories: their own GitHub Apps and tokens, and the company's.
    Each has an id (what ``cairnml repository assign`` takes), its type, name, scope, owner and state. No secret is returned."""
    result = _person_request(session, user_access.GetAllRequest())
    if not result.ok():
        raise DatasetError(f"the server did not list the credentials: {_reason(result)}")
    return (result.response_data or {}).get("credentials") or []


def fetch_repository_list(session=None) -> list:
    """The repositories the signed-in user sees (repositories.get_all): code, datasets and registries. Each has its ``roles``, its
    owner, whether it is ``shared``, the projects it is listed in, every ``assignment`` on it (yours are editable, the others'
    read-only) and ``access``: how you read it now. The server answers from its own records. Raises DatasetError when the server
    cannot be asked, for instance when there is no CairnML login."""
    try:
        result = _session(session).send(repositories.GetAllRequest())
        if not result.ok():
            raise DatasetError(f"the server refused the repository list: {_reason(result)}")
        return (result.response_data or {}).get("repositories") or []
    except DatasetError:
        raise
    except Exception as exc:  # no login, no network, a malformed answer: all mean the server cannot answer
        raise DatasetError(_could_not_ask(exc, " for the repository list", LOGIN_HINT_FOR_LIST)) from exc

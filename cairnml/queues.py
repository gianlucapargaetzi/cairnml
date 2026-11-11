"""
Read the queues of the server and the workers that listen to them.

A task enqueued to a queue waits there until a worker listening to that queue takes it::

    from cairnml import queues

    for queue in queues.list_queues():
        print(queue.name)

    if not queues.workers_for_queue("gpu"):
        print("no worker is listening to 'gpu'")
"""

import difflib
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from .backend_api.services import queues as queues_service, workers as workers_service
from .backend_interface.base import InterfaceBase


@dataclass(frozen=True)
class Queue:
    """A queue on the server."""

    id: str
    name: str


@dataclass(frozen=True)
class Worker:
    """A worker (a running agent) and the names of the queues it listens to."""

    id: str
    queues: Tuple[str, ...]


def _session(session: Optional[Any]) -> Any:
    return session if session is not None else InterfaceBase._get_default_session()


def list_queues(session: Optional[Any] = None) -> List[Queue]:
    """
    List the queues of the server.

    :param session: The session to use. Defaults to the session of this machine.
    :return: The queues, in the order the server returns them.
    """
    res = _session(session).send(queues_service.GetAllRequest(only_fields=["id", "name"])).wait()
    return [Queue(id=q["id"], name=q.get("name", "")) for q in (res.response_data or {}).get("queues", [])]


def list_workers(session: Optional[Any] = None) -> List[Worker]:
    """
    List the workers that the server has seen recently, with the names of the queues each one listens to.

    :param session: The session to use. Defaults to the session of this machine.
    :return: The workers.
    """
    res = _session(session).send(workers_service.GetAllRequest()).wait()
    workers = []
    for worker in (res.response_data or {}).get("workers", []):
        names = tuple(q.get("name", "") for q in worker.get("queues") or [])
        workers.append(Worker(id=worker["id"], queues=names))
    return workers


def workers_for_queue(queue_name: str, session: Optional[Any] = None) -> List[Worker]:
    """
    List the workers that listen to a queue. The list is empty when no worker listens to it.

    :param queue_name: The name of the queue.
    :param session: The session to use. Defaults to the session of this machine.
    :return: The workers listening to the queue.
    """
    return [worker for worker in list_workers(session=session) if queue_name in worker.queues]


def unknown_queue_message(queue_name: str, session: Optional[Any] = None) -> str:
    """
    The text of the error for a queue name the server does not know: it lists the queues the account can see and
    names the closest ones, because an agent's queue is its machine name plus a short suffix and is easy to mistype.

    :param queue_name: The name that was not found.
    :param session: The session to use. Defaults to the session of this machine.
    :return: The message. It never raises: if the queues cannot be read, it only says the queue was not found.
    """
    message = f'Could not find queue named "{queue_name}".'
    # noinspection PyBroadException
    try:
        names = sorted({q.name for q in list_queues(session=session) if q.name})
    except Exception:
        return message
    if not names:
        return f"{message} No queue is visible to this account."
    close = [n for n in names if n.startswith(queue_name) or queue_name.startswith(n)]
    close += [n for n in difflib.get_close_matches(queue_name, names, n=3, cutoff=0.6) if n not in close]
    if close:
        message += f' Did you mean "{close[0]}"?' if len(close) == 1 else f" Did you mean one of: {', '.join(close)}?"
    return f"{message} Queues you can use: {', '.join(names)}. The name is shown under Agents & Queues → Queues."

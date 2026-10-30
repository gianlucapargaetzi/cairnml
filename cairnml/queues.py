"""
Read the queues of the server and the workers that listen to them.

A task enqueued to a queue waits there until a worker listening to that queue takes it::

    from cairnml import queues

    for queue in queues.list_queues():
        print(queue.name)

    if not queues.workers_for_queue("gpu"):
        print("no worker is listening to 'gpu'")
"""

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

"""The job's token file: written by the agent, and renewed in the background while the job runs. The job reads it
through cairnml_agent/backend_api/session/job_token_file.py."""

import os
import tempfile
import threading
from pathlib import Path

from cairnml_agent.backend_api.session import Request

TOKEN_FILE_NAME = "job_token"
# Where docker mode mounts the folder with the file, inside the job container.
CONTAINER_DIR = "/root/.cairnml/job-token"
# A renewal is asked for this fraction of the token's lifetime after it was issued, so it lands before the expiry.
RENEW_AT_FRACTION = 0.8
# A renewal that failed for another reason (for example the network) is tried again after this many seconds.
RETRY_SEC = 30


def request_job_token(session, task_id):
    """The job's token and its lifetime in seconds, issued by the server to this agent while the task runs here.
    None when the server refuses, for example once the task has ended. Errors are raised."""
    res = session.send_request(
        service='devices', action='job_token', version='2.8', method=Request.def_method,
        json={'task': task_id},
    )
    if res is None or not res.ok:
        return None
    data = res.json()['data']
    return data['access_token'], data.get('expires_in', 3600)


def write_job_token(path: Path, token: str) -> None:
    """Replace the file with the token, atomically, readable by its owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".job_token.")
    with os.fdopen(fd, "w") as f:
        f.write(token)
    os.chmod(tmp, 0o600)
    os.replace(tmp, str(path))


class TokenRenewer(threading.Thread):
    """Renews the job's token until stopped, or until the server refuses a renewal (the task has ended).

    ``fetch`` returns ``(token, expires_in)``, or None when the server refuses."""

    def __init__(self, path: Path, fetch, expires_in: float, label: str = "", retry_sec: float = RETRY_SEC):
        super().__init__(name="job-token-renewer", daemon=True)
        self._path = path
        self._label = label
        self._fetch = fetch
        self._retry_sec = retry_sec
        self._wait = self._wait_for(expires_in)
        self._stopped = threading.Event()

    @staticmethod
    def _wait_for(expires_in: float) -> float:
        return max(expires_in * RENEW_AT_FRACTION, 1.0)

    def run(self):
        print("Job token of task {} renews in {:.0f} s".format(self._label, self._wait), flush=True)
        while not self._stopped.wait(self._wait):
            try:
                renewed = self._fetch()
            except Exception as ex:
                print("Job token renewal failed for task {}, trying again: {}".format(self._label, ex), flush=True)
                self._wait = self._retry_sec
                continue
            if renewed is None:
                print("Job token renewal refused for task {}; the job's token is not renewed".format(self._label), flush=True)
                return
            token, expires_in = renewed
            write_job_token(self._path, token)
            print("Job token renewed for task {}".format(self._label), flush=True)
            self._wait = self._wait_for(expires_in)

    def stop(self) -> None:
        self._stopped.set()

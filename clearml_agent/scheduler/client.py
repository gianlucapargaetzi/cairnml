"""The agent's calls to the scheduler endpoints. Each call is made with the agent's device token."""


class SchedulerError(Exception):
    pass


class SchedulerClient(object):
    VERSION = "2.36"

    def __init__(self, session):
        self._session = session

    def _post(self, action: str, body: dict) -> dict:
        res = self._session.send_request(service="scheduler", action=action, version=self.VERSION, json=body)
        if res is None or not res.ok:
            text = res.text[:300] if res is not None else "no response"
            raise SchedulerError("{}: {}".format(action, text))
        return res.json()["data"]

    def register(self, worker: str, capacity: dict, reserved: dict, can_enforce: dict, agent_version: str):
        return self._post("register_worker", {
            "worker": worker, "capacity": capacity, "reserved": reserved,
            "can_enforce": can_enforce, "agent_version": agent_version,
        })

    def sync(self, worker: str, jobs: list, usage: dict) -> dict:
        return self._post("sync", {"worker": worker, "jobs": jobs, "usage": usage})

    def claim(self, worker: str, task: str) -> dict:
        return self._post("claim", {"worker": worker, "task": task})

    def release(self, worker: str, task: str, outcome: str, failure: dict = None) -> dict:
        body = {"worker": worker, "task": task, "outcome": outcome}
        if failure:
            body["failure"] = failure
        return self._post("release", body)

    def job_token(self, task: str) -> str:
        res = self._session.send_request(service="devices", action="job_token", version="2.8", json={"task": task})
        if res is None or not res.ok:
            raise SchedulerError("the job token for {} was refused".format(task))
        return res.json()["data"]["access_token"]

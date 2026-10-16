from clearml_agent.scheduler.client import SchedulerError
from clearml_agent.scheduler.daemon import ManagedDaemon

CAPACITY = {"cpus": 8, "ram_gb": 32, "disk_gb": 200, "gpus": [{"index": 0, "model": "L40S", "memory_gb": 48}]}
RESERVED = {"cpus": 1, "ram_gb": 2, "disk_gb": 10}


class FakeClient:
    def __init__(self, offers=(), stop=(), claim_fails=False):
        self.offers = list(offers)
        self.stop = list(stop)
        self.claim_fails = claim_fails
        self.claimed, self.released = [], []

    def register(self, *a):
        return {"id": "w", "state": "active"}

    def sync(self, worker, jobs, usage):
        offers, self.offers = self.offers, []
        return {"offers": offers, "stop": self.stop}

    def claim(self, worker, task):
        if self.claim_fails:
            raise SchedulerError("no offer")
        self.claimed.append(task)
        return {"task": task}

    def release(self, worker, task, outcome, failure=None):
        self.released.append((task, outcome, failure))
        return {}


class FakeRunner:
    def __init__(self):
        self.started, self.running, self.done, self.stopped = [], {}, [], []

    def sample(self):
        pass

    def stop_orphans(self):
        return []

    def running_tasks(self):
        return list(self.running)

    def usage_of(self, task):
        return {}

    def start(self, task, reservation, max_runtime_min=None):
        self.started.append((task, max_runtime_min))
        self.running[task] = reservation

    def stop(self, task):
        self.stopped.append(task)
        self.running.pop(task, None)

    def end(self, task):
        self.running.pop(task, None)

    def finished(self):
        out, self.done = self.done, []
        for r in out:
            self.running.pop(r["task"], None)
        return out


def daemon(client, runner):
    return ManagedDaemon(client, "w", CAPACITY, RESERVED, {"docker": True}, runner)


def offer(task, cpus=(2, 3), ram=8, exclusive=False, runtime=None):
    return {"task": task, "cpus": list(cpus), "gpus": [], "ram_gb": ram, "disk_gb": 10,
            "exclusive": exclusive, "max_runtime_min": runtime}


def test_an_offer_is_claimed_then_run():
    client, runner = FakeClient(offers=[offer("t1", runtime=30)]), FakeRunner()
    daemon(client, runner).tick()
    assert client.claimed == ["t1"]
    assert runner.started == [("t1", 30)]


def test_an_offer_that_overlaps_a_running_job_waits_until_it_finishes():
    runner = FakeRunner()
    client = FakeClient(offers=[offer("t1", cpus=(2, 3))])
    d = daemon(client, runner)
    d.tick()
    client.offers = [offer("t2", cpus=(3, 4))]  # CPU 3 is in use by t1
    d.tick()
    assert client.claimed == ["t1"]
    runner.done = [{"task": "t1", "outcome": "completed", "failure": None}]
    client.offers = [offer("t2", cpus=(3, 4))]
    d.tick()
    assert client.claimed == ["t1", "t2"]


def test_an_offer_already_claimed_for_is_not_repeated():
    runner = FakeRunner()
    client = FakeClient(offers=[offer("t1")])
    d = daemon(client, runner)
    d.tick()
    client.offers = [offer("t1")]
    d.tick()
    assert client.claimed == ["t1"]


def test_a_refused_claim_starts_nothing():
    runner = FakeRunner()
    client = FakeClient(offers=[offer("t1")], claim_fails=True)
    daemon(client, runner).tick()
    assert runner.started == []


def test_an_exclusive_offer_waits_for_an_empty_machine():
    runner = FakeRunner()
    client = FakeClient(offers=[offer("t1")])
    d = daemon(client, runner)
    d.tick()
    client.offers = [offer("whole", cpus=range(1, 8), exclusive=True, ram=28)]
    d.tick()
    assert client.claimed == ["t1"]  # the whole-machine offer is not claimed while t1 runs


def test_a_finished_job_is_released_with_its_outcome_and_failure():
    runner = FakeRunner()
    client = FakeClient(offers=[offer("t1")])
    d = daemon(client, runner)
    d.tick()
    runner.done = [{"task": "t1", "outcome": "failed",
                    "failure": {"reason": "oom", "detail": "Failure due to bad compute: out of memory"}}]
    d.tick()
    assert client.released == [("t1", "failed", {"reason": "oom",
                                                 "detail": "Failure due to bad compute: out of memory"})]


def test_a_stop_from_the_server_stops_the_running_job():
    runner = FakeRunner()
    client = FakeClient(offers=[offer("t1")])
    d = daemon(client, runner)
    d.tick()
    client.stop = ["t1"]
    d.tick()
    assert runner.stopped == ["t1"]


def test_a_job_that_cannot_start_is_released_as_failed_and_the_daemon_goes_on():
    class BrokenRunner(FakeRunner):
        def start(self, task, reservation, max_runtime_min=None):
            raise OSError("no such file")

    client = FakeClient(offers=[offer("t1"), offer("t2", cpus=(4, 5))])
    daemon(client, BrokenRunner()).tick()
    assert [r[0:2] for r in client.released] == [("t1", "failed"), ("t2", "failed")]


def test_a_daemon_keeps_trying_to_register_until_the_server_accepts_it():
    class Flaky(FakeClient):
        attempts = 0

        def register(self, *a):
            Flaky.attempts += 1
            if Flaky.attempts < 3:
                raise SchedulerError("server not ready")
            return {"id": "w", "state": "active"}

    class Stop(Exception):
        pass

    client = Flaky()
    d = daemon(client, FakeRunner())
    d._sleep = lambda seconds: (_ for _ in ()).throw(Stop()) if Flaky.attempts >= 3 else None
    try:
        d.run_forever()
    except Stop:
        pass
    assert Flaky.attempts == 3


def test_a_job_that_ended_on_its_own_is_killed_without_being_marked_stopped():
    class Recording(FakeRunner):
        ended = []

        def end(self, task):
            Recording.ended.append(task)
            self.running.pop(task, None)

    runner = Recording()
    client = FakeClient(offers=[offer("t1")])
    d = daemon(client, runner)
    d.tick()
    client.ended = None
    client2 = FakeClient()
    d.client = client2
    original_sync = client2.sync

    def sync_with_ended(worker, jobs, usage):
        reply = original_sync(worker, jobs, usage)
        reply["ended"] = ["t1"]
        return reply

    client2.sync = sync_with_ended
    d.tick()
    assert Recording.ended == ["t1"]
    assert runner.stopped == []

"""The managed daemon: one per machine. Registers the machine, then every sync interval it reports its
usage, takes the offers that fit, runs their jobs, and reports how each ended."""

import logging
import time
from typing import Callable, Optional

from clearml_agent.scheduler.client import SchedulerError
from clearml_agent.scheduler.ledger import LocalLedger

log = logging.getLogger("clearml_agent.managed")


class ManagedDaemon(object):
    def __init__(self, client, worker: str, capacity: dict, reserved: dict, can_enforce: dict, runner,
                 agent_version: str = "", sync_interval_sec: float = 10,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.client = client
        self.worker = worker
        self.capacity = capacity
        self.reserved = reserved
        self.can_enforce = can_enforce
        self.runner = runner
        self.agent_version = agent_version
        self.sync_interval_sec = sync_interval_sec
        self._sleep = sleep
        cpu_ids = range(reserved["cpus"], capacity["cpus"])
        gpu_ids = [g["index"] for g in capacity.get("gpus", [])]
        schedulable = {"cpus": capacity["cpus"] - reserved["cpus"],
                       "ram_gb": capacity["ram_gb"] - reserved["ram_gb"],
                       "disk_gb": capacity["disk_gb"] - reserved["disk_gb"]}
        self.ledger = LocalLedger(schedulable, cpu_ids, gpu_ids)

    def register(self):
        self.client.register(self.worker, self.capacity, self.reserved, self.can_enforce, self.agent_version)

    def tick(self, machine_usage: Optional[dict] = None) -> dict:
        self.runner.sample()
        running = self.runner.running_tasks()
        jobs = [{"task": t, "usage": self.runner.usage_of(t)} for t in running]
        reply = self.client.sync(self.worker, jobs, machine_usage or {})

        for task in reply.get("stop") or []:
            if task in running:
                self.runner.stop(task)

        # finished jobs first, so the capacity they held is free for the offers below
        for result in self.runner.finished():
            task = result["task"]
            self.ledger.release(task)
            try:
                self.client.release(self.worker, task, result["outcome"], result.get("failure"))
            except SchedulerError as ex:
                log.warning("could not report the end of %s: %s", task, ex)

        for offer in reply.get("offers") or []:
            task = offer["task"]
            if task in running or not self.ledger.fits(offer):
                continue
            self._start_offer(offer)
        return reply

    def _start_offer(self, offer: dict):
        task = offer["task"]
        try:
            self.client.claim(self.worker, task)
        except SchedulerError as ex:
            log.info("offer for %s not claimed: %s", task, ex)
            return
        try:
            self.runner.start(task, offer, max_runtime_min=offer.get("max_runtime_min"))
        except OSError as ex:
            self.client.release(self.worker, task, "failed", {
                "reason": "error", "detail": f"Could not start the job: {ex}"})
            return
        self.ledger.hold(task, offer)

    def run_forever(self, machine_usage: Callable[[], dict] = lambda: {}):
        stopped = self.runner.stop_orphans()
        if stopped:
            log.warning("stopped %d job container(s) left by an earlier daemon", len(stopped))
        self.register()
        while True:
            try:
                self.tick(machine_usage())
            except SchedulerError as ex:
                log.warning("server: %s", ex)
            self._sleep(self.sync_interval_sec)


class MachineSampler(object):
    """Machine usage for each sync: CPU percent since the last call, RAM and disk in use."""

    def __init__(self, workdir: str, proc_root: str = "/proc"):
        from clearml_agent.scheduler import usage as _usage
        self._usage = _usage
        self._workdir = workdir
        self._proc = proc_root
        self._last = None

    def __call__(self) -> dict:
        with open(f"{self._proc}/stat", encoding="utf-8") as h:
            times = self._usage.parse_cpu_times(h.read())
        cpu = self._usage.cpu_percent(self._last, times) if self._last else 0.0
        self._last = times
        with open(f"{self._proc}/meminfo", encoding="utf-8") as h:
            mem = self._usage.parse_meminfo(h.read())
        return {"cpu_percent": round(cpu, 1), "ram_used_gb": round(mem["ram_used_gb"], 2),
                "disk_used_percent": round(self._usage.disk_used_percent(self._workdir), 1)}

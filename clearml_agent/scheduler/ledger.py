"""The agent's own record of what its running jobs hold. It refuses an offer that would overcommit the
machine, even if the server thinks it fits, so a stale view cannot oversubscribe a machine."""


class LocalLedger(object):
    def __init__(self, schedulable: dict, all_cpu_ids, all_gpu_indices):
        self.schedulable = schedulable  # {cpus, ram_gb, disk_gb}
        self._cpus = set(all_cpu_ids)
        self._gpus = set(all_gpu_indices)
        self._held = {}  # task -> reservation

    def fits(self, offer: dict) -> bool:
        if offer.get("exclusive"):
            return not self._held
        if any(r.get("exclusive") for r in self._held.values()):
            return False
        cpus = set(offer.get("cpus") or [])
        gpus = set(offer.get("gpus") or [])
        if not cpus <= self._cpus or not gpus <= self._gpus:
            return False
        if cpus & self._used_cpus() or gpus & self._used_gpus():
            return False
        ram = sum(r.get("ram_gb") or 0 for r in self._held.values()) + (offer.get("ram_gb") or 0)
        disk = sum(r.get("disk_gb") or 0 for r in self._held.values()) + (offer.get("disk_gb") or 0)
        return ram <= self.schedulable["ram_gb"] + 1e-6 and disk <= self.schedulable["disk_gb"] + 1e-6

    def hold(self, task: str, offer: dict):
        self._held[task] = dict(offer)

    def release(self, task: str):
        self._held.pop(task, None)

    def held(self):
        return set(self._held)

    def _used_cpus(self):
        return {c for r in self._held.values() for c in (r.get("cpus") or [])}

    def _used_gpus(self):
        return {g for r in self._held.values() for g in (r.get("gpus") or [])}

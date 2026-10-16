from clearml_agent.scheduler.ledger import LocalLedger

SCHED = {"cpus": 7, "ram_gb": 28, "disk_gb": 180}


def ledger():
    return LocalLedger(SCHED, all_cpu_ids=range(1, 8), all_gpu_indices=[0, 1])


def test_two_jobs_with_disjoint_cpus_fit():
    lg = ledger()
    lg.hold("a", {"cpus": [1, 2], "gpus": [], "ram_gb": 8, "disk_gb": 10})
    assert lg.fits({"cpus": [3, 4], "gpus": [], "ram_gb": 8, "disk_gb": 10})


def test_an_overlapping_cpu_is_refused():
    lg = ledger()
    lg.hold("a", {"cpus": [1, 2], "gpus": [], "ram_gb": 8, "disk_gb": 10})
    assert not lg.fits({"cpus": [2, 3], "gpus": [], "ram_gb": 8, "disk_gb": 10})


def test_too_much_ram_is_refused():
    lg = ledger()
    lg.hold("a", {"cpus": [1], "gpus": [], "ram_gb": 20, "disk_gb": 10})
    assert not lg.fits({"cpus": [2], "gpus": [], "ram_gb": 10, "disk_gb": 10})


def test_a_gpu_already_held_is_refused():
    lg = ledger()
    lg.hold("a", {"cpus": [1], "gpus": [0], "ram_gb": 1, "disk_gb": 1})
    assert not lg.fits({"cpus": [2], "gpus": [0], "ram_gb": 1, "disk_gb": 1})


def test_an_exclusive_offer_needs_an_empty_machine_and_blocks_others():
    lg = ledger()
    assert lg.fits({"cpus": list(range(1, 8)), "gpus": [], "exclusive": True})
    lg.hold("a", {"cpus": [1], "gpus": [], "ram_gb": 1, "disk_gb": 1})
    assert not lg.fits({"cpus": list(range(1, 8)), "gpus": [], "exclusive": True})
    lg.release("a")
    lg.hold("whole", {"cpus": list(range(1, 8)), "gpus": [], "exclusive": True})
    assert not lg.fits({"cpus": [1], "gpus": [], "ram_gb": 1, "disk_gb": 1})


def test_releasing_frees_the_resources():
    lg = ledger()
    lg.hold("a", {"cpus": [1, 2], "gpus": [], "ram_gb": 8, "disk_gb": 10})
    lg.release("a")
    assert lg.fits({"cpus": [1, 2], "gpus": [], "ram_gb": 8, "disk_gb": 10})

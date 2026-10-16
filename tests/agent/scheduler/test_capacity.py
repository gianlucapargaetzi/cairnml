from clearml_agent.scheduler.capacity import can_enforce, detect, parse_nvidia_smi, reserve_for

MEMINFO = "MemTotal:       67108864 kB\nMemAvailable:   50000000 kB\n"  # 64 GiB


def test_parses_gpus_and_converts_mib_to_gb():
    gpus = parse_nvidia_smi("0, NVIDIA L40S, 49152\n1, NVIDIA L40S, 49152\n")
    assert gpus == [{"index": 0, "model": "NVIDIA L40S", "memory_gb": 48.0},
                    {"index": 1, "model": "NVIDIA L40S", "memory_gb": 48.0}]


def test_no_gpu_lines_gives_none():
    assert parse_nvidia_smi("No devices were found\n") == []


def test_detect_reads_ram_from_meminfo(tmp_path):
    cap = detect(str(tmp_path), MEMINFO, gpus=[], cpus=32)
    assert cap["cpus"] == 32
    assert cap["ram_gb"] == 64.0
    assert cap["disk_gb"] > 0


def test_default_reserve_is_one_cpu_and_ten_percent():
    res = reserve_for({"cpus": 32, "ram_gb": 64, "disk_gb": 800})
    assert res == {"cpus": 1, "ram_gb": 7, "disk_gb": 80}


def test_reserve_keeps_at_least_one_cpu_for_jobs_on_a_one_cpu_machine():
    assert reserve_for({"cpus": 1, "ram_gb": 4, "disk_gb": 10})["cpus"] == 0


def test_configured_reserve_overrides_the_default():
    res = reserve_for({"cpus": 32, "ram_gb": 64, "disk_gb": 800}, {"cpus": 4, "ram_percent": 25})
    assert res["cpus"] == 4
    assert res["ram_gb"] == 16


def test_enforcement_needs_docker_and_the_nvidia_runtime_for_gpu_limits():
    assert can_enforce(True, True, True) == {"docker": True, "cpuset": True, "gpu_device_limit": True}
    assert can_enforce(True, True, False)["gpu_device_limit"] is False
    assert can_enforce(False, True, True) == {"docker": False, "cpuset": False, "gpu_device_limit": False}

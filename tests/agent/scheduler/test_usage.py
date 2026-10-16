import os
import tempfile

from clearml_agent.scheduler.usage import (
    container_cgroup_dirs,
    cpu_percent,
    parse_cpu_times,
    parse_cpu_usage_usec,
    parse_docker_stats,
    parse_meminfo,
    read_container_usage,
)


def test_reads_memory_peak_and_cpu_from_a_cgroup_v2_scope():
    with tempfile.TemporaryDirectory() as root:
        scope = os.path.join(root, "system.slice", "docker-abc.scope")
        os.makedirs(scope)
        open(os.path.join(scope, "memory.current"), "w").write(str(2 * 2**30) + "\n")
        open(os.path.join(scope, "memory.peak"), "w").write(str(3 * 2**30) + "\n")
        open(os.path.join(scope, "cpu.stat"), "w").write("usage_usec 2500000\nuser_usec 1\n")
        usage = read_container_usage("abc", root=root)
    assert usage["ram_gb"] == 2.0
    assert usage["peak_ram_gb"] == 3.0
    assert usage["cpu_seconds"] == 2.5


def test_missing_cgroup_gives_none():
    with tempfile.TemporaryDirectory() as root:
        assert read_container_usage("nope", root=root) is None


def test_the_fallback_paths_are_listed():
    dirs = container_cgroup_dirs("x", root="/sys/fs/cgroup")
    assert dirs[0].endswith("docker-x.scope")
    assert dirs[1].endswith("docker/x")


def test_docker_stats_line():
    parsed = parse_docker_stats("12.50%  1.5GiB / 62.2GiB")
    assert parsed["cpu_percent"] == 12.5
    assert round(parsed["ram_gb"], 2) == round(1.5 * 2**30 / 1e9, 2)
    assert parse_docker_stats("garbage") is None


def test_meminfo_gives_total_and_used():
    text = "MemTotal:       65536000 kB\nMemAvailable:   16384000 kB\n"
    m = parse_meminfo(text)
    assert round(m["ram_total_gb"], 1) == round(65536000 * 1024 / 2**30, 1)
    assert m["ram_used_gb"] > 0


def test_cpu_percent_from_two_samples():
    before = parse_cpu_times("cpu  100 0 100 800 0 0 0 0\n")
    after = parse_cpu_times("cpu  200 0 200 1000 0 0 0 0\n")
    assert cpu_percent(before, after) == 50.0


def test_cpu_usage_usec_parser():
    assert parse_cpu_usage_usec("usage_usec 42\n") == 42
    assert parse_cpu_usage_usec("") == 0

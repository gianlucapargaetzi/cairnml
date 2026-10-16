import shlex
from clearml_agent.scheduler.enforce import compact_ranges, docker_limit_args, job_env, memory_arg


def test_compact_ranges_joins_runs():
    assert compact_ranges([0, 1, 2, 5]) == "0-2,5"
    assert compact_ranges([4]) == "4"
    assert compact_ranges([7, 3, 4, 5]) == "3-5,7"
    assert compact_ranges([]) == ""


def test_memory_is_whole_megabytes():
    assert memory_arg(12) == "12288m"
    assert memory_arg(0.5) == "512m"


def test_a_shared_job_gets_cpu_memory_gpu_and_pid_limits():
    args = docker_limit_args({"cpus": [2, 3, 4, 5], "ram_gb": 12, "gpus": [1], "exclusive": False}, "t1")
    assert args[:2] == ["--cpuset-cpus", "2-5"]
    assert ["--memory", "12288m"] == args[2:4]
    assert ["--memory-swap", "12288m"] == args[4:6]
    assert args[-2:] == ["--pids-limit", "1024"]


def test_an_exclusive_job_gets_no_memory_limit():
    args = docker_limit_args({"cpus": [2, 3], "ram_gb": 12, "gpus": [], "exclusive": True}, "t1")
    assert "--memory" not in args


def test_job_env_carries_the_label_and_round_trips_through_shlex():
    env = job_env({"cpus": [0, 1], "ram_gb": 2, "gpus": [], "exclusive": False}, "task42")
    assert env["CLEARML_AGENT_EXTRA_DOCKER_LABELS"] == "clearml.job=task42"
    assert shlex.split(env["CLEARML_AGENT_EXTRA_DOCKER_ARGS"])[:2] == ["--cpuset-cpus", "0-1"]


def test_the_gpus_of_a_job_come_from_the_environment_so_the_agent_adds_no_gpu_flag():
    env = job_env({"cpus": [0], "gpus": [1, 2], "ram_gb": 1, "disk_gb": 1, "exclusive": False}, "t")
    assert env["NVIDIA_VISIBLE_DEVICES"] == "1,2"
    assert job_env({"cpus": [0], "gpus": [], "ram_gb": 1, "disk_gb": 1}, "t")["NVIDIA_VISIBLE_DEVICES"] == "none"

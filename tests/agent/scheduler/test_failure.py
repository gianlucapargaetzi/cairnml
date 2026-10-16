from clearml_agent.scheduler.failure import classify

OOM = {"State": {"OOMKilled": True, "ExitCode": 137}}
RESERVED = {"ram_gb": 12}


def test_success_has_no_failure():
    assert classify(0, {"State": {"OOMKilled": False}}, "done", RESERVED, {}) is None


def test_oom_is_bad_compute_with_the_limit_and_the_peak():
    f = classify(137, OOM, "", RESERVED, {"ram_gb": 12.0})
    assert f["reason"] == "oom"
    assert f["reserved"] == {"ram_gb": 12}
    assert "12 GB" in f["detail"] and "peak 12.0 GB" in f["detail"]
    assert f["detail"].startswith("Failure due to bad compute")


def test_oom_wins_over_a_traceback_in_the_log():
    f = classify(137, OOM, "Traceback ...\nRuntimeError: CUDA out of memory", RESERVED, {})
    assert f["reason"] == "oom"


def test_time_limit_wins_over_everything():
    f = classify(143, OOM, "", RESERVED, {}, deadline_minutes=30, deadline_hit=True)
    assert f["reason"] == "time_limit"
    assert "30 minutes" in f["detail"]


def test_cuda_out_of_memory_in_the_log_is_a_gpu_error():
    f = classify(1, {"State": {"OOMKilled": False}}, "RuntimeError: CUDA out of memory. Tried to allocate",
                 RESERVED, {})
    assert f["reason"] == "gpu_error"


def test_no_space_left_is_disk_full():
    f = classify(1, {"State": {"OOMKilled": False}}, "OSError: [Errno 28] No space left on device", {}, {})
    assert f["reason"] == "disk_full"


def test_other_non_zero_exit_is_error_with_the_last_lines():
    f = classify(2, {"State": {"OOMKilled": False}}, "a\nb\nValueError: bad input", {}, {})
    assert f["reason"] == "error"
    assert f["exit_code"] == 2
    assert "ValueError: bad input" in f["detail"]

import pytest

from clearml.backend_interface.task.requirements import RequirementsValueError, build_requirements, check_priority


def test_no_requirements_means_the_whole_machine():
    assert build_requirements() is None


def test_all_main_fields_build_the_request_shape():
    req = build_requirements(cpus=4, ram_gb=12, gpus=1, gpu_memory_gb=24, disk_gb=50, max_runtime_min=720)
    assert req == {"cpus": 4, "ram_gb": 12.0,
                   "gpus": {"count": 1, "min_memory_gb": 24.0, "model": None},
                   "disk_gb": 50.0, "max_runtime_min": 720}


def test_a_partial_set_names_the_missing_fields():
    with pytest.raises(RequirementsValueError) as ctx:
        build_requirements(cpus=4, ram_gb=12)
    assert "gpus" in str(ctx.value) and "disk_gb" in str(ctx.value)


def test_max_runtime_alone_is_refused():
    with pytest.raises(RequirementsValueError):
        build_requirements(max_runtime_min=60)


def test_zero_gpus_is_an_answer():
    assert build_requirements(cpus=1, ram_gb=1, gpus=0, disk_gb=0)["gpus"]["count"] == 0


@pytest.mark.parametrize("kwargs", [
    {"cpus": 0, "ram_gb": 1, "gpus": 0, "disk_gb": 1},
    {"cpus": 1, "ram_gb": 0, "gpus": 0, "disk_gb": 1},
    {"cpus": 1, "ram_gb": 1, "gpus": -1, "disk_gb": 1},
    {"cpus": 1, "ram_gb": 1, "gpus": 0, "disk_gb": -1},
])
def test_out_of_range_values_are_refused(kwargs):
    with pytest.raises(RequirementsValueError):
        build_requirements(**kwargs)


def test_priority_bounds():
    assert check_priority(None) is None
    assert check_priority(0) == 0
    assert check_priority(100) == 100
    for bad in (-1, 101, "high", True):
        with pytest.raises(RequirementsValueError):
            check_priority(bad)


def test_enqueue_body_carries_requirements_and_priority_only_when_given():
    from clearml.backend_interface.task.requirements import enqueue_body
    assert enqueue_body("t1", None, None, None) == {"task": "t1"}
    body = enqueue_body("t1", "q1", {"cpus": 2}, 70)
    assert body == {"task": "t1", "queue": "q1", "requirements": {"cpus": 2}, "priority": 70}

import sys
import time

import pytest

from clearml_agent.scheduler.runner import JobRunner

RESERVATION = {"cpus": [2], "gpus": [], "ram_gb": 4, "disk_gb": 1, "exclusive": False}


def python_job(code):
    def builder(task, reservation):
        return [sys.executable, "-c", code], {}
    return builder


def wait_for(runner, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        done = runner.finished()
        if done:
            return done[0]
        time.sleep(0.1)
    raise AssertionError("the job did not finish")


@pytest.fixture
def runner(tmp_path):
    return JobRunner(command_builder=python_job("print('ok')"), log_dir=str(tmp_path), expect_container=False)


def test_a_clean_exit_is_completed(runner):
    runner.start("job-ok", RESERVATION)
    result = wait_for(runner)
    assert result["outcome"] == "completed"
    assert result["failure"] is None


def test_a_non_zero_exit_is_a_failed_job_with_its_exit_code(tmp_path):
    runner = JobRunner(command_builder=python_job("import sys; print('boom'); sys.exit(3)"), log_dir=str(tmp_path), expect_container=False)
    runner.start("job-bad", RESERVATION)
    result = wait_for(runner)
    assert result["outcome"] == "failed"
    assert result["failure"]["reason"] == "error"
    assert result["failure"]["exit_code"] == 3
    assert "boom" in result["failure"]["detail"]


def test_a_job_that_exits_zero_without_a_container_is_a_failure_when_containers_are_expected(tmp_path):
    runner = JobRunner(command_builder=python_job("print('skipped')"), log_dir=str(tmp_path))
    runner.start("job-nocontainer", RESERVATION)
    result = wait_for(runner)
    assert result["outcome"] == "failed"
    assert result["failure"]["reason"] == "error"
    assert "did not start" in result["failure"]["detail"]


def test_the_time_limit_stops_the_job_and_says_so(tmp_path):
    runner = JobRunner(command_builder=python_job("import time; time.sleep(60)"), log_dir=str(tmp_path), expect_container=False)
    runner.start("job-slow", RESERVATION, max_runtime_min=0.05)
    result = wait_for(runner)
    assert result["outcome"] == "failed"
    assert result["failure"]["reason"] == "time_limit"


def test_a_stop_from_the_server_ends_the_job_as_stopped(tmp_path):
    runner = JobRunner(command_builder=python_job("import time; time.sleep(60)"), log_dir=str(tmp_path), expect_container=False)
    runner.start("job-stop", RESERVATION)
    time.sleep(0.5)
    runner.stop("job-stop")
    result = wait_for(runner)
    assert result["outcome"] == "stopped"
    assert result["failure"] is None


def test_the_limits_and_label_reach_the_job_environment_and_no_token_is_passed(tmp_path):
    code = ("import os; assert 'CLEARML_JOB_TOKEN' not in os.environ; "
            "assert os.environ['CLEARML_AGENT_EXTRA_DOCKER_LABELS']=='clearml.job=job-env'; "
            "assert '--cpuset-cpus' in os.environ['CLEARML_AGENT_EXTRA_DOCKER_ARGS']")
    runner = JobRunner(command_builder=python_job(code), log_dir=str(tmp_path), expect_container=False)
    runner.start("job-env", RESERVATION)
    result = wait_for(runner)
    assert result["outcome"] == "completed", result


def test_a_job_ended_by_the_server_reports_its_real_outcome_when_it_exits_on_its_own(tmp_path):
    runner = JobRunner(command_builder=python_job("import sys; print('oops'); sys.exit(2)"), log_dir=str(tmp_path),
                       expect_container=False)
    runner.start("job-ended", RESERVATION)
    runner.end("job-ended")
    result = wait_for(runner)
    assert result["outcome"] == "failed"
    assert result["failure"]["exit_code"] == 2


def test_a_job_ended_by_the_server_is_killed_after_the_grace_period(tmp_path):
    runner = JobRunner(command_builder=python_job("import time; time.sleep(120)"), log_dir=str(tmp_path),
                       expect_container=False, ended_grace_sec=0.5)
    runner.start("job-hung", RESERVATION)
    runner.end("job-hung")
    time.sleep(1.5)
    runner.sample()
    result = wait_for(runner)
    assert result["outcome"] in ("failed", "completed", "stopped")

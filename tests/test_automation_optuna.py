"""
Timing of the Optuna objective loop (cairnml.automation.optuna.optuna), with a fake optuna module,
a fake job and a fake clock: no optuna install and no wall-clock sleeping are needed.
"""

import importlib
import inspect
import sys
import types

import pytest

from cairnml.task import Task

MODULE_NAMES = ("cairnml.automation.optuna.optuna", "cairnml.automation.optuna")


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds


class FakeJob:
    def __init__(self, clock, finish_at):
        self.clock = clock
        self.finish_at = finish_at
        self.status_check_times = []

    def launch(self, queue_name=None):
        return True

    def is_pending(self):
        return False

    def task_id(self):
        return "fake-job"

    def abort(self):
        pass

    def is_stopped(self, **_):
        self.status_check_times.append(self.clock.now)
        return self.clock.now >= self.finish_at


class FakeMetric:
    """Objective metric: reports (iteration, value) from ``value_from`` on, ``None`` before that."""

    len = 1
    FINAL_VALUE = 0.75

    def __init__(self, clock, value_from=0.0):
        self.clock = clock
        self.value_from = value_from
        self.read_times = []

    def get_current_raw_objective(self, job):
        self.read_times.append(self.clock.now)
        if len(self.read_times) > 1000:
            raise RuntimeError("the objective is polled without waiting (busy loop)")
        if self.clock.now < self.value_from:
            return None
        return [(1, 0.5)]

    def get_objective(self, job):
        return [self.FINAL_VALUE]


class FakeTrial:
    def report(self, value, step):
        pass

    def should_prune(self):
        return False


@pytest.fixture
def opt(monkeypatch):
    fake = types.ModuleType("optuna")
    fake.Trial = object
    fake.Study = object
    exceptions = types.ModuleType("optuna.exceptions")
    exceptions.TrialPruned = type("TrialPruned", (Exception,), {})
    fake.exceptions = exceptions
    fake.TrialPruned = exceptions.TrialPruned
    monkeypatch.setitem(sys.modules, "optuna", fake)
    monkeypatch.setitem(sys.modules, "optuna.exceptions", exceptions)
    monkeypatch.setattr(Task, "add_requirements", lambda *args, **kwargs: None)
    for name in MODULE_NAMES:
        monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module("cairnml.automation.optuna.optuna")
    yield module
    for name in MODULE_NAMES:
        sys.modules.pop(name, None)


def run_objective(opt_module, monkeypatch, finish_at, value_from=0.0, sleep_interval=120):
    """Run one trial objective; return (clock, job, metric, returned objective value)."""
    clock = FakeClock()
    monkeypatch.setattr(opt_module, "sleep", clock.sleep)
    job = FakeJob(clock, finish_at)
    metric = FakeMetric(clock, value_from=value_from)

    class Optimizer:
        def __init__(self):
            self.parameter_override_history = []
            self._current_jobs = []
            self._objective_metric = metric
            self.budget = types.SimpleNamespace(jobs=types.SimpleNamespace(update=lambda *a, **k: None))

        def helper_create_job(self, base_task_id, parameter_override):
            return job

        def monitor_job(self, current_job):
            return not current_job.is_stopped()

    objective = opt_module.OptunaObjective(
        base_task_id="base",
        queue_name="queue",
        optimizer=Optimizer(),
        max_iteration_per_job=None,
        min_iteration_per_job=None,
        sleep_interval=sleep_interval,
        config_space={},
    )
    returned = objective.objective(FakeTrial())
    return clock, job, metric, returned


def test_running_job_is_read_once_per_period(opt, monkeypatch):
    _, _, metric, _ = run_objective(opt, monkeypatch, finish_at=300)
    assert metric.read_times[:3] == [0.0, 120.0, 240.0]


def test_default_pool_period_is_two_minutes(opt):
    default = inspect.signature(opt.OptimizerOptuna.__init__).parameters["pool_period_min"].default
    assert default == 2.0


def test_status_is_checked_at_most_once_per_slice_of_a_period(opt, monkeypatch):
    _, job, _, _ = run_objective(opt, monkeypatch, finish_at=10000)
    checks_in_first_period = [t for t in job.status_check_times if 0 < t <= 120]
    assert 1 <= len(checks_in_first_period) <= 120 / 5 + 2

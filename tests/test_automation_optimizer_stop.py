"""HyperParameterOptimizer.stop() and its reporter thread, with a fake search (cairnml#189)."""

import threading
from time import time
from types import SimpleNamespace

import pytest

from cairnml.automation.optimization import HyperParameterOptimizer

# The reporter's first report is at about 15 seconds; a later report is at the 5-minute default period.
FIRST_REPORT_TIMEOUT = 60
JOIN_TIMEOUT = 30


class _FakeLogger:
    def report_scalar(self, *args, **kwargs):
        pass

    def report_text(self, *args, **kwargs):
        pass


class _FakeTask:
    id = "fake-optimizer-task"

    def get_logger(self):
        return _FakeLogger()


class _FakeSearch:
    """A search that runs until its test ends it (``done`` is set) or until ``stop()`` is called."""

    def __init__(self):
        self.done = threading.Event()
        self.budget = SimpleNamespace(compute_time=SimpleNamespace(used=None, limit=None))

    def set_optimizer_task(self, task):
        pass

    def set_job_default_parent(self, *args, **kwargs):
        pass

    def set_job_class(self, job_class):
        pass

    def start(self):
        self.done.wait()

    def stop(self):
        self.done.set()

    def get_running_jobs(self):
        return []

    def get_created_jobs_ids(self):
        return {}

    def _get_child_tasks(self, **kwargs):
        return []


@pytest.fixture
def make_optimizer():
    """Build a HyperParameterOptimizer with the real constructor, a fake search and a fake task."""
    created = []

    def make():
        search = _FakeSearch()
        optimizer = HyperParameterOptimizer(
            base_task_id="fake-base-task",
            hyper_parameters=[],
            objective_metric_title="title",
            objective_metric_series="series",
            optimizer_class=search,
        )
        optimizer._task = _FakeTask()
        optimizer._readonly_task = False
        optimizer.reports = []
        optimizer.first_report = threading.Event()

        def record_report(*args, **kwargs):
            # search_done: whether the search had ended when this report was written
            optimizer.reports.append({"search_done": search.done.is_set(), "forced": kwargs.get("force", False)})
            optimizer.first_report.set()

        optimizer._report_completed_status = record_report
        optimizer._report_remaining_budget = lambda *args, **kwargs: None
        optimizer._report_resources = lambda *args, **kwargs: None
        optimizer._report_completed_tasks_best_results = lambda *args, **kwargs: None
        optimizer._auto_archive_low_performance_tasks = lambda *args, **kwargs: None
        created.append((optimizer, search))
        return optimizer, search

    yield make

    for optimizer, search in created:
        search.done.set()
        if optimizer._thread_reporter is not None:
            optimizer._thread_reporter.join(timeout=JOIN_TIMEOUT)


def _start_and_wait_for_first_report(optimizer):
    optimizer.start()
    assert optimizer.first_report.wait(timeout=FIRST_REPORT_TIMEOUT), "the reporter made no first report"


def _run_with_timeout(function, seconds):
    """Run ``function`` in a helper thread; fail the test instead of hanging when it does not return in time."""
    worker = threading.Thread(target=function, daemon=True)
    started = time()
    worker.start()
    worker.join(timeout=seconds)
    assert not worker.is_alive(), f"the call did not return within {seconds} s"
    return time() - started


def test_default_report_period_is_five_minutes(make_optimizer):
    optimizer, _ = make_optimizer()

    assert optimizer._report_period_min == 5.0


def test_stop_before_start_returns_at_once(make_optimizer):
    optimizer, _ = make_optimizer()

    _run_with_timeout(optimizer.stop, JOIN_TIMEOUT)

    assert optimizer.reports == []


def test_stop_while_search_runs_joins_reporter_and_sends_final_report(make_optimizer):
    optimizer, _ = make_optimizer()
    _start_and_wait_for_first_report(optimizer)
    reporter = optimizer._thread_reporter
    reports_before_stop = len(optimizer.reports)

    _run_with_timeout(optimizer.stop, JOIN_TIMEOUT)

    assert not reporter.is_alive()
    assert len(optimizer.reports) > reports_before_stop


def test_stop_twice_returns_at_once_the_second_time(make_optimizer):
    optimizer, _ = make_optimizer()
    _start_and_wait_for_first_report(optimizer)
    _run_with_timeout(optimizer.stop, JOIN_TIMEOUT)
    reports_after_first_stop = len(optimizer.reports)

    elapsed = _run_with_timeout(optimizer.stop, 5)

    assert elapsed < 5
    assert len(optimizer.reports) == reports_after_first_stop


def test_stop_without_waiting_for_reporter_returns_without_joining(make_optimizer):
    optimizer, _ = make_optimizer()
    _start_and_wait_for_first_report(optimizer)
    reporter = optimizer._thread_reporter

    _run_with_timeout(lambda: optimizer.stop(wait_for_reporter=False), JOIN_TIMEOUT)

    # the reporter still ends on its own once it has been woken
    reporter.join(timeout=JOIN_TIMEOUT)
    assert not reporter.is_alive()

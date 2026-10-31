from collections import deque
from types import SimpleNamespace

import pytest

from cairnml_agent.helper import resource_monitor
from cairnml_agent.helper.resource_monitor import ResourceMonitor
from cairnml_agent.version import __version__


class Recording:
    """A session stub that records the status reports sent to the server."""

    def __init__(self):
        self.sent = []

    def get(self, service, action, **report):
        self.sent.append((service, action, report))


def monitor_with(session, report):
    monitor = ResourceMonitor.__new__(ResourceMonitor)
    monitor.session = session
    monitor.queue = deque(maxlen=1)
    monitor.queue.appendleft(report)
    monitor._worker_id = "agent-1"
    monitor._worker_tags = ["gpu"]
    monitor._report_daemon = True
    monitor._run_mode = "docker"
    return monitor


def test_a_report_says_who_is_alive_what_runs_and_which_version_it_is():
    session = Recording()
    monitor = monitor_with(session, ResourceMonitor.StatusReport(
        task="task-1", queue="default", queues=["default", "cpu"]))
    assert monitor.send_report(stats={"cpu_usage": [10.0]})

    service, action, report = session.sent[0]
    assert (service, action) == ("workers", "status_report")
    assert report["worker"] == "agent-1"
    assert report["task"] == "task-1"
    assert report["queue"] == "default"
    assert report["queues"] == ["default", "cpu"]
    assert report["agent_version"] == __version__
    assert isinstance(report["timestamp"], int)


def test_an_idle_agent_still_reports_its_queues_and_version():
    session = Recording()
    monitor = monitor_with(session, ResourceMonitor.StatusReport(queues=["default"]))
    monitor.send_report()
    report = session.sent[0][2]
    assert report["queues"] == ["default"]
    assert "task" not in report
    assert report["agent_version"] == __version__


def test_a_report_says_where_the_agent_runs_jobs():
    session = Recording()
    monitor = monitor_with(session, ResourceMonitor.StatusReport(queues=["default"]))
    monitor._run_mode = "venv"
    monitor.send_report()
    assert session.sent[0][2]["run_mode"] == "venv"


def test_a_report_without_a_known_run_mode_sends_none():
    session = Recording()
    monitor = monitor_with(session, ResourceMonitor.StatusReport(queues=["default"]))
    monitor._run_mode = None
    monitor.send_report()
    assert session.sent[0][2]["run_mode"] is None


def test_an_unreadable_machine_capacity_does_not_stop_the_report(monkeypatch):
    def broken(_path):
        raise OSError("no disk")

    monkeypatch.setattr(resource_monitor.psutil, "disk_usage", broken)
    session = Recording()
    monitor = monitor_with(session, ResourceMonitor.StatusReport(queues=["default"]))
    assert monitor.send_report()
    capacity = session.sent[0][2]["machine_capacity"]
    assert "disk_gb" not in capacity and capacity["cpus"] >= 0


def test_one_failing_round_does_not_end_the_reporting_thread(monkeypatch):
    session = Recording()
    monitor = monitor_with(session, ResourceMonitor.StatusReport(queues=["default"]))
    monitor._sample_frequency = 1000.0
    monitor._report_frequency = 0.01
    monitor._first_report_sec = 0.01
    monitor._readouts = {}
    monitor._cluster_report = None
    monitor._fix_active_gpus = lambda: None
    monitor._update_readouts = lambda: None
    monitor._exit_event = resource_monitor.Event()
    monitor._clear_readouts = lambda: None

    rounds = []

    def averages():
        rounds.append(1)
        if len(rounds) == 1:
            raise RuntimeError("a bad sample")
        if len(rounds) >= 3:
            monitor._exit_event.set()
        return {}

    monitor._get_average_readouts = averages
    monitor._daemon()
    assert len(rounds) >= 3
    assert len(session.sent) >= 2

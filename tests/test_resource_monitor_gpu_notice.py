"""The GPU notice is written to the task log, but not to the job's stdout."""

from types import SimpleNamespace
from unittest import mock

import pytest

from cairnml.utilities.resource_monitor import ResourceMonitor


class _Logger:
    def __init__(self):
        self.calls = []

    def report_text(self, msg, *args, **kwargs):
        self.calls.append((msg, kwargs))


@pytest.fixture
def monitor_without_gpu():
    logger = _Logger()
    monitor = ResourceMonitor.__new__(ResourceMonitor)
    monitor._task = SimpleNamespace(get_logger=lambda: logger)
    monitor._gpustat = object()
    monitor._gpustat_fail = 0
    monitor._debug_mode = False
    monitor._process_info = None
    monitor._get_process_used_memory = mock.Mock(return_value=0)
    monitor._get_gpu_stats = mock.Mock(side_effect=RuntimeError("no NVML"))
    return monitor, logger


def test_gpu_notice_is_logged_without_printing_to_console(monitor_without_gpu, capsys):
    monitor, logger = monitor_without_gpu
    for _ in range(3):
        monitor._machine_stats()

    notices = [(msg, kw) for msg, kw in logger.calls if "switching off GPU monitoring" in msg]
    assert len(notices) == 1
    assert notices[0][1].get("print_console") is False
    assert monitor._gpustat is None
    assert "GPU monitoring" not in capsys.readouterr().out

"""Matplotlib figures become interactive plotly plots when plotly is installed (the extra cairnml[plot]), and images
when it is not. The tests use the figure reporting of PatchedMatplotlib with a fake reporter and logger."""

import sys
from unittest import mock

import pytest

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from cairnml.binding.matplotlib_bind import PatchedMatplotlib  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_renderer(monkeypatch):
    monkeypatch.setattr(PatchedMatplotlib, "_support_image_plot", True)
    monkeypatch.setattr(PatchedMatplotlib, "_matplotlylib", None)
    monkeypatch.setattr(PatchedMatplotlib, "_plotly_renderer", None)
    monkeypatch.setattr(PatchedMatplotlib, "_plotly_missing_reported", False)
    yield
    plt.close("all")


class FakeReporter:
    def __init__(self):
        self.plots = []

    def report_plot(self, **kwargs):
        self.plots.append(kwargs)


class FakeLogger:
    def __init__(self):
        self.images = []

    def _report_image_plot_and_upload(self, *args, **kwargs):
        self.images.append(kwargs or args)

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _report(figure, title):
    reporter, logger = FakeReporter(), FakeLogger()
    PatchedMatplotlib._report_figure(
        reporter=reporter, logger=logger, title=title, series=title, iter=0, specific_fig=figure
    )
    return reporter, logger


def _line():
    figure, axes = plt.subplots()
    axes.plot([1, 2, 3], [3, 1, 2], label="a")
    axes.set_title("Line")
    axes.set_xlabel("x")
    axes.set_ylabel("y")
    axes.legend()
    return figure


def _scatter():
    figure, axes = plt.subplots()
    x = np.arange(10)
    axes.scatter(x, x**2)
    axes.set_title("Scatter")
    return figure


def _bar():
    figure, axes = plt.subplots()
    axes.bar(["a", "b"], [3, 1])
    axes.set_title("Bar")
    return figure


def _two_subplots():
    figure, axes = plt.subplots(1, 2)
    axes[0].plot([1, 2], [1, 2])
    axes[1].hist(np.random.RandomState(0).randn(50))
    return figure


def _imshow():
    figure, axes = plt.subplots()
    axes.imshow(np.arange(16).reshape(4, 4))
    return figure


def test_a_line_plot_is_an_interactive_plot_with_its_titles_and_axes():
    reporter, _ = _report(_line(), "line")
    assert len(reporter.plots) == 1
    plot = reporter.plots[0]["plot"]
    assert [trace["type"] for trace in plot["data"]] == ["scatter"]
    assert plot["layout"]["title"] == "line"
    assert plot["layout"]["xaxis"]["title"]["text"] == "x"
    assert plot["layout"]["yaxis"]["title"]["text"] == "y"
    assert "template" not in plot["layout"]


def test_a_scatter_plot_is_an_interactive_plot():
    reporter, _ = _report(_scatter(), "scatter")
    assert [trace["type"] for trace in reporter.plots[0]["plot"]["data"]] == ["scatter"]


def test_a_bar_chart_is_an_interactive_plot():
    reporter, _ = _report(_bar(), "bar")
    assert [trace["type"] for trace in reporter.plots[0]["plot"]["data"]] == ["bar"]


def test_two_subplots_are_sent_as_an_image_as_they_always_were():
    reporter, logger = _report(_two_subplots(), "subplots")
    assert reporter.plots == []
    assert len(logger.images) == 1


def test_an_image_is_sent_as_an_image():
    reporter, logger = _report(_imshow(), "imshow")
    assert reporter.plots == []
    assert len(logger.images) == 1


def test_without_plotly_each_figure_is_an_image_and_the_extra_is_named_once(monkeypatch):
    monkeypatch.setitem(sys.modules, "plotly", None)  # "from plotly import ..." then fails, as without the extra
    base_logger = mock.Mock()
    monkeypatch.setattr("cairnml.debugging.log.LoggerRoot.get_base_logger", classmethod(lambda cls, **kw: base_logger))

    first_reporter, first_logger = _report(_line(), "first")
    second_reporter, second_logger = _report(_bar(), "second")

    assert first_reporter.plots == [] and second_reporter.plots == []
    assert len(first_logger.images) == 1 and len(second_logger.images) == 1
    named = [c for c in base_logger.warning.call_args_list if "cairnml[plot]" in c.args[0]]
    assert len(named) == 1

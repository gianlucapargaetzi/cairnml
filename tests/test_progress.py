import io
from types import SimpleNamespace

import pytest

from cairnml.binding import progress
from cairnml.binding.progress import IDLE, PatchTqdm, ProgressBar, ProgressReporter, ProgressTracker


@pytest.fixture
def tracker(monkeypatch):
    fresh = ProgressTracker()
    monkeypatch.setattr(progress, "tracker", fresh)
    return fresh


def labels(state):
    return [bar["label"] for bar in state["bars"]]


def test_the_progress_is_the_outermost_bar_and_the_inner_bar_fills_its_step(tracker):
    epochs = tracker.start("epochs", 10)
    tracker.update(epochs, 2)
    batches = tracker.start("batches", 100)
    tracker.update(batches, 40)
    state = tracker.state()
    assert state["percent"] == 24  # (2 + 0.4) / 10
    assert state["bars"] == [
        {"label": "epochs", "n": 2, "total": 10},
        {"label": "batches", "n": 40, "total": 100},
    ]


def test_a_bar_without_a_label_is_called_step(tracker):
    first = tracker.start(None, 4)
    inner = tracker.start("", 3)
    unnamed_inner_of_a_named_bar = None
    assert (first.label, inner.label) == ("Step 1", "Step 1.1")
    tracker.end(inner)
    tracker.end(first)
    assert tracker.start(None, 2).label == "Step 2"
    named = tracker.start("epochs", 5)
    unnamed_inner_of_a_named_bar = tracker.start(None, 5)
    assert unnamed_inner_of_a_named_bar.label == "Step 1" and named.label == "epochs"


def test_a_finished_outer_bar_is_replaced_by_the_next_one(tracker):
    loading = tracker.start("loading", 5)
    tracker.update(loading, 5)
    tracker.end(loading)
    assert tracker.state() == IDLE  # nothing is running between the two bars
    epochs = tracker.start("epochs", 4)
    state = tracker.state()
    assert labels(state) == ["epochs"] and state["percent"] == 0


def test_a_pinned_bar_hides_the_others(tracker):
    tracker.set_pinned("Epochs")
    loading = tracker.start("loading", 5)
    tracker.update(loading, 3)
    assert tracker.state() == IDLE and not tracker.pinned_seen  # a bar that does not count: idle
    tracker.end(loading)
    epochs = tracker.start("epochs:", 10)  # tqdm descriptions often end with a colon
    inner = tracker.start("batches", 10)
    tracker.update(inner, 5)
    state = tracker.state()
    assert tracker.pinned_seen
    assert labels(state) == ["epochs", "batches"] and state["percent"] == 5


def test_a_pinned_bar_inside_another_still_counts(tracker):
    tracker.start("outer", 2)
    epochs = tracker.start("epochs", 10)
    tracker.update(epochs, 5)
    tracker.set_pinned("epochs")
    state = tracker.state()
    assert labels(state) == ["epochs"] and state["percent"] == 50


def test_the_percentage_never_goes_back_inside_a_bar(tracker):
    epochs = tracker.start("epochs", 4)
    tracker.update(epochs, 1)
    batches = tracker.start("batches", 10)
    tracker.update(batches, 9)
    assert tracker.state()["percent"] == 47  # (1 + 0.9) / 4
    tracker.end(batches)
    validation = tracker.start("validation", 10)  # a second bar inside the same step starts at 0 again
    tracker.update(validation, 2)
    assert tracker.state()["percent"] == 47  # it does not go back
    tracker.update(epochs, 3)
    assert tracker.state()["percent"] == 80  # (3 + 0.2) / 4


def test_without_a_total_there_is_no_percentage(tracker):
    stream = tracker.start("stream", None)
    tracker.update(stream, 7)
    state = tracker.state()
    assert state["percent"] is None and state["bars"][0]["n"] == 7 and state["bars"][0]["total"] is None


def test_only_the_outermost_and_three_levels_inside_are_reported(tracker):
    for level in range(6):
        tracker.start(f"level {level}", 10)
    assert len(tracker.state()["bars"]) == progress.MAX_LEVELS


def test_task_progress_wraps_a_loop(tracker):
    seen = []
    for _ in ProgressBar(range(4), label="steps"):
        seen.append(tracker.state()["percent"])
    assert seen == [0, 25, 50, 75]
    assert tracker.state() == IDLE  # the loop is over


def test_task_progress_counts_by_hand_and_as_a_context_manager(tracker):
    with ProgressBar(total=8, label="items") as bar:
        bar.update(2)
        assert tracker.state()["percent"] == 25
    assert tracker._bars == {}  # bars that are over are forgotten


def test_tqdm_bars_are_found_without_changing_the_script(tracker):
    tqdm = pytest.importorskip("tqdm")
    PatchTqdm.patch()
    states = []
    for epoch in tqdm.tqdm(range(4), desc="epochs", file=io.StringIO(), mininterval=0):
        for batch in tqdm.tqdm(range(10), desc=f"epoch {epoch}", file=io.StringIO(), mininterval=0, leave=False):
            if batch == 4 and epoch == 1:
                states.append(tracker.state())
    assert states[0]["percent"] == 35  # (1 + 4/10) / 4: the inner bar has counted 4 steps
    assert labels(states[0])[0] == "epochs"
    assert tracker.state() == IDLE


def test_a_disabled_tqdm_bar_is_not_reported(tracker):
    tqdm = pytest.importorskip("tqdm")
    PatchTqdm.patch()
    for _ in tqdm.tqdm(range(3), disable=True):
        assert tracker.state() is None


class FakeTask:
    id = "task-1"

    def __init__(self, status=200):
        self.sent, self.status = [], status
        self.session = self

    def send(self, request, **kwargs):
        self.sent.append(request.to_dict())
        return SimpleNamespace(meta=SimpleNamespace(result_code=self.status))


def test_the_reporter_sends_changes_but_not_more_often_than_asked(tracker):
    task = FakeTask()
    reporter = ProgressReporter(task, tracker, send_every=1000)
    bar = tracker.start("epochs", 10)
    reporter.report()
    tracker.update(bar, 5)
    reporter.report()  # too soon
    assert [sent["percent"] for sent in task.sent] == [0]
    reporter.report(final=True)  # the last state is always sent
    assert [sent["percent"] for sent in task.sent] == [0, 50]
    reporter.report(final=True)  # nothing changed
    assert len(task.sent) == 2


def test_the_reporter_gives_up_on_a_server_without_the_endpoint(tracker):
    task = FakeTask(status=400)
    reporter = ProgressReporter(task, tracker, send_every=0)
    bar = tracker.start("epochs", 100)
    for step in range(10):
        tracker.update(bar, step)
        reporter.report()
    assert len(task.sent) == progress.GIVE_UP_AFTER


def test_task_init_takes_a_progress_bar_and_task_has_progress():
    import inspect

    from cairnml import Task

    assert inspect.signature(Task.init).parameters["progress_bar"].default is None
    assert isinstance(Task.progress(range(2), label="x"), ProgressBar)


def test_a_failing_session_does_not_stop_the_run_and_is_given_up_on(tracker):
    class Broken(FakeTask):
        def send(self, request, **kwargs):
            self.sent.append(request)
            raise RuntimeError("no response class")

    task = Broken()
    reporter = ProgressReporter(task, tracker, send_every=0)
    bar = tracker.start("epochs", 100)
    for step in range(10):
        tracker.update(bar, step)
        reporter.report()
    assert len(task.sent) == progress.GIVE_UP_AFTER


def test_the_response_of_the_request_is_registered():
    from cairnml.backend_api.services.v2_23 import tasks
    from cairnml.backend_api.utils import get_response_cls

    assert get_response_cls(tasks.UpdateProgressRequest) is tasks.UpdateProgressResponse


def test_nothing_is_reported_until_a_bar_was_started(tracker):
    assert tracker.state() is None


def test_after_the_pinned_bar_is_over_the_run_is_idle(tracker):
    tracker.set_pinned("epochs")
    epochs = tracker.start("epochs", 2)
    tracker.update(epochs, 1)
    assert tracker.state()["percent"] == 50
    tracker.end(epochs)
    saving = tracker.start("saving", 3)  # a loop that is not the one that counts
    tracker.update(saving, 1)
    assert tracker.state() == IDLE


def test_the_reporter_sends_the_idle_state_once(tracker):
    task = FakeTask()
    reporter = ProgressReporter(task, tracker, send_every=0)
    bar = tracker.start("epochs", 4)
    tracker.update(bar, 2)
    reporter.report()
    tracker.end(bar)
    reporter.report()
    reporter.report()
    assert [(sent.get("percent"), len(sent.get("bars") or [])) for sent in task.sent] == [(50, 1), (None, 0)]

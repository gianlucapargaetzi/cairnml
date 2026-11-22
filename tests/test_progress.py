import io
import os
import pathlib
import signal
import subprocess
import sys
import zipfile
from types import SimpleNamespace

import pytest

import cairnml
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
    tracker.start("epochs", 4)
    state = tracker.state()
    assert labels(state) == ["epochs"] and state["percent"] == 0


def test_a_pinned_bar_hides_the_others(tracker):
    tracker.set_pinned("Epochs")
    loading = tracker.start("loading", 5)
    tracker.update(loading, 3)
    assert tracker.state() == IDLE and not tracker.pinned_seen  # a bar that does not count: idle
    tracker.end(loading)
    tracker.start("epochs:", 10)  # tqdm descriptions often end with a colon
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
    from cairnml.backend_api.services import tasks
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


def test_a_bar_at_its_total_that_is_never_closed_is_idle_not_100(tracker):
    bar = tracker.start("epochs", 10)
    tracker.update(bar, 10)
    assert tracker.state() == IDLE


def test_one_step_below_the_total_is_99_not_100(tracker):
    bar = tracker.start("epochs", 100)
    tracker.update(bar, 99)
    assert tracker.state()["percent"] == 99


def test_past_the_total_is_idle_not_100(tracker):
    bar = tracker.start("epochs", 10)
    tracker.update(bar, 11)
    assert tracker.state() == IDLE


def test_a_bar_at_its_total_with_a_running_inner_bar_is_never_100(tracker):
    outer = tracker.start("epochs", 10)
    tracker.update(outer, 10)
    inner = tracker.start("batches", 100)
    tracker.update(inner, 50)
    assert tracker.state()["percent"] != 100


def test_closing_a_bar_at_its_total_stays_idle(tracker):
    bar = tracker.start("epochs", 10)
    tracker.update(bar, 10)
    tracker.end(bar)
    assert tracker.state() == IDLE


class _Warnings:
    def __init__(self):
        self.messages = []

    def warning(self, message, *args, **kwargs):
        self.messages.append(message)


@pytest.fixture
def warnings_of_the_run(monkeypatch):
    from cairnml.debugging.log import LoggerRoot

    recorded = _Warnings()
    monkeypatch.setattr(LoggerRoot, "get_base_logger", classmethod(lambda cls, *a, **k: recorded))
    return recorded


def test_a_pinned_bar_that_never_starts_is_warned_about_when_the_run_is_local(
    tracker, monkeypatch, warnings_of_the_run
):
    monkeypatch.setattr(progress, "_handed_over", False)
    tracker.set_pinned("epochs")
    progress.detach()
    assert any("No progress bar named 'epochs' was started" in m for m in warnings_of_the_run.messages)


def test_no_warning_when_the_run_was_queued_for_an_agent(tracker, monkeypatch, warnings_of_the_run):
    monkeypatch.setattr(progress, "_handed_over", False)
    tracker.set_pinned("epochs")
    progress.hand_over()
    progress.detach()
    assert not any("No progress bar" in m for m in warnings_of_the_run.messages)


# A script that queues itself and leaves: the real Task.init, close and exit run offline; only the calls that talk to
# the server are replaced. A live run is in_progress on the server, an offline one stays "created".
_QUEUEING_SCRIPT = """
import sys
from cairnml import Task

Task.set_offline(True)
task = Task.init(project_name="p", task_name="t", progress_bar="epochs", auto_connect_frameworks=False)
Task.status = property(lambda self: Task.TaskStatusEnum.in_progress)
task._find_queue_or_fail = lambda name: "queue-id"
task.mark_stopped = lambda *a, **k: None
task.get_output_log_web_page = lambda: "http://stub/log"
task.get_system_tags = lambda: []
task.set_system_tags = lambda *a, **k: None
task.reset = lambda *a, **k: None
Task.clone = classmethod(lambda cls, *a, **k: task)
Task.enqueue = staticmethod(lambda *a, **k: print("ENQUEUED", flush=True))

mode = sys.argv[1]
if mode == "queue":
    task.execute_remotely(queue_name="q")
elif mode == "queue_clone":
    task.execute_remotely(queue_name="q", clone=True)
elif mode == "reset_and_exit":
    task.execute_remotely()
print("END OF SCRIPT", flush=True)
"""


def _run_the_script(mode, tmp_path):
    import os
    import pathlib
    import subprocess
    import sys

    import cairnml

    script = tmp_path / "script.py"
    script.write_text(_QUEUEING_SCRIPT)
    home = tmp_path / "home"
    home.mkdir()
    # the logger's reporting subprocess can be lost at exit and hang the interpreter (log.py, "HANGS IF RACE HOLDS");
    # the thread mode is used instead, so the run ends and the output is complete
    (home / "cairnml.conf").write_text("sdk { development { report_use_subprocess: false } }\n")
    env = dict(os.environ)
    env.update(
        HOME=str(home),
        CAIRNML_OFFLINE_MODE="1",
        PYTHONPATH=str(pathlib.Path(cairnml.__file__).resolve().parent.parent),
    )
    result = subprocess.run(
        [sys.executable, str(script), mode],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["queue", "queue_clone", "reset_and_exit"])
def test_no_warning_when_a_script_queues_itself_with_execute_remotely(mode, tmp_path):
    output = _run_the_script(mode, tmp_path)
    assert "No progress bar named" not in output, output
    assert "END OF SCRIPT" not in output, output
    if mode != "reset_and_exit":
        assert "Switching to remote execution" in output, output


def test_a_never_started_bar_in_a_script_that_does_not_queue_still_warns(tmp_path):
    output = _run_the_script("local", tmp_path)
    assert "No progress bar named 'epochs' was started" in output, output
    assert "END OF SCRIPT" in output, output


# A repository check that is still running when the log reporter forks: the check's spawn of git holds an exec-error
# pipe until the spawn returns. The reporter inherits that pipe, so the wait for the check runs to its timeout (#172).
_REPORTER_FORK_WINDOW_SCRIPT = """
import os
import subprocess
import threading

from cairnml import Task

real_fork_exec = subprocess._fork_exec
real_fork = os.fork
spawned = threading.Event()
reporter_forked = threading.Event()


def paused_fork_exec(*args, **kwargs):
    pid = real_fork_exec(*args, **kwargs)
    if threading.current_thread() is not threading.main_thread():
        spawned.set()
        reporter_forked.wait(10)
    return pid


def fork_then_signal():
    pid = real_fork()
    if pid != 0 and spawned.is_set():
        reporter_forked.set()
    return pid


subprocess._fork_exec = paused_fork_exec
os.fork = fork_then_signal

Task.set_offline(True)
task = Task.init(project_name="p", task_name="t", progress_bar="epochs", auto_connect_frameworks=False)
thread = task._detect_repo_async_thread
task._wait_for_repo_detection(timeout=10)
print("DETECTION STILL RUNNING AFTER WAIT" if thread and thread.is_alive() else "DETECTION DONE", flush=True)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="the log reporter is a forked subprocess (posix only)")
def test_a_repo_check_that_starts_while_the_reporter_forks_does_not_hold_up_the_exit(tmp_path):
    script = tmp_path / "script.py"
    script.write_text(_REPORTER_FORK_WINDOW_SCRIPT)
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env.update(
        HOME=str(home),
        CAIRNML_OFFLINE_MODE="1",
        PYTHONPATH=str(pathlib.Path(cairnml.__file__).resolve().parent.parent),
    )
    # own session, so a hung reporter subprocess is killed with the script
    process = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(tmp_path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
        pytest.fail(f"the script did not finish within 60 s:\n{output}")
    assert "DETECTION DONE" in output, output


# The reporter subprocess keeps the queue pipes it reads from and drops the rest of the inherited descriptors (#172);
# its records must still reach the offline session: text in the log, a scalar in the metrics, both in the zip at close.
_RECORDS_SCRIPT = """
from cairnml import Task

Task.set_offline(True)
task = Task.init(project_name="p", task_name="t", progress_bar="epochs", auto_connect_frameworks=False)
task.get_logger().report_text("records-of-the-reporter")
task.get_logger().report_scalar("series-of-the-reporter", "value", 1.5, 3)
task.close()
print("END OF SCRIPT", flush=True)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="the log reporter is a forked subprocess (posix only)")
def test_the_reporter_still_writes_its_log_and_metrics_after_it_drops_the_inherited_descriptors(tmp_path):
    script = tmp_path / "script.py"
    script.write_text(_RECORDS_SCRIPT)
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env.update(
        HOME=str(home),
        CAIRNML_OFFLINE_MODE="1",
        PYTHONPATH=str(pathlib.Path(cairnml.__file__).resolve().parent.parent),
    )
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "END OF SCRIPT" in result.stdout, result.stdout + result.stderr
    archives = list((home / ".cairnml" / "cache" / "offline").glob("*.zip"))
    assert len(archives) == 1, result.stdout + result.stderr
    with zipfile.ZipFile(archives[0]) as archive:
        log = archive.read("log.jsonl").decode()
        metrics = archive.read("metrics.jsonl").decode()
    assert "records-of-the-reporter" in log, log
    assert "series-of-the-reporter" in metrics, metrics

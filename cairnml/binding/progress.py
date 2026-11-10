"""
Progress of a run, found while the script runs.

Every progress bar the script uses -- a tqdm bar (patched automatically) or ``Task.progress`` -- is registered
with one tracker. The tracker follows the *outermost* bar and the bars inside it:

- the run's progress is the outermost active bar, plus the part of its current step that the bar inside it has
  done, so it moves smoothly: epoch 3 of 10 with 40% of the batches done is 24%;
- with ``Task.init(progress_bar="epochs")`` only the bar with that label counts, and bars outside it ("loading")
  are not reported; the bars inside it still are;
- the value never goes back while the same bar is running;
- while no bar that counts is running (loading data, saving the model, a loop that is not the pinned one) the state is
  "idle": no bars and no percentage, which the web app shows as an animation instead of a bar.

A background thread sends the state to the server when it changed, at most every couple of seconds.
"""

import os
import threading
import time
import weakref
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from .. import Task

MAX_LEVELS = 4  # the outermost bar and the bars inside it that are reported
SEND_EVERY = 2.0  # seconds: the fastest the server hears about a change
CHECK_EVERY = 0.5  # seconds: how often the reporter looks for a change
GIVE_UP_AFTER = 3  # failed reports in a row, e.g. a server without the endpoint


class _Bar:
    """One progress bar. A tqdm bar is read live from the tqdm object; others keep their own count."""

    def __init__(self, bar_id: int, label: str, total: Optional[int], parent: Optional["_Bar"], source: Any = None):
        self.id = bar_id
        self.label = label
        self._total = total
        self._n = 0
        self.parent = parent
        self.children: List["_Bar"] = []
        self.started_children = 0
        self.closed = False
        self._source = weakref.ref(source) if source is not None else None

    @property
    def n(self) -> float:
        source = self._source() if self._source else None
        return getattr(source, "n", self._n) if source is not None else self._n

    @n.setter
    def n(self, value: float) -> None:
        self._n = value

    @property
    def total(self) -> Optional[float]:
        source = self._source() if self._source else None
        total = getattr(source, "total", self._total) if source is not None else self._total
        return total if total else None

    @total.setter
    def total(self, value: Optional[float]) -> None:
        self._total = value

    @property
    def done(self) -> bool:
        total = self.total
        return self.closed or (total is not None and self.n >= total)

    def depth(self) -> int:
        return 0 if self.parent is None else self.parent.depth() + 1


class ProgressTracker:
    """The bars of this process. All methods are cheap: they only note the change; nothing is sent from here."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._bars: Dict[int, _Bar] = {}
        self._counter = 0
        self._roots_started = 0
        self._pinned: Optional[str] = None
        self._pinned_seen = False
        self._last_root: Optional[int] = None
        self._best = 0
        self._started_any = False
        self.changed = False

    # --- what the script does -------------------------------------------------------------------------------------

    def set_pinned(self, label: Optional[str]) -> None:
        with self._lock:
            self._pinned = (label or "").strip().lower() or None

    @property
    def pinned(self) -> Optional[str]:
        return self._pinned

    @property
    def pinned_seen(self) -> bool:
        return self._pinned_seen

    def start(self, label: Optional[str] = None, total: Optional[int] = None, source: Any = None) -> _Bar:
        with self._lock:
            parent = self._enclosing()
            self._counter += 1
            label = (label or "").strip().rstrip(":").strip()
            if not label:
                if parent is None:
                    self._roots_started += 1
                    label = f"Step {self._roots_started}"
                elif parent.label.startswith("Step "):
                    label = f"{parent.label}.{parent.started_children + 1}"
                else:
                    label = f"Step {parent.started_children + 1}"
            bar = _Bar(self._counter, label, total, parent, source)
            self._started_any = True
            if parent is not None:
                parent.children.append(bar)
                parent.started_children += 1
            self._bars[bar.id] = bar
            if self._pinned and bar.label.lower() == self._pinned:
                self._pinned_seen = True
            self.changed = True
            return bar

    def touch(self, bar: Optional[_Bar]) -> None:
        if bar is not None:
            self.changed = True

    def update(self, bar: _Bar, n: float, total: Optional[int] = None) -> None:
        bar.n = n
        if total is not None:
            bar.total = total
        self.changed = True

    def end(self, bar: Optional[_Bar]) -> None:
        if bar is None:
            return
        with self._lock:
            bar.closed = True
            self.changed = True
            self._prune()

    # --- what is reported -----------------------------------------------------------------------------------------

    def _enclosing(self) -> Optional[_Bar]:
        """The bar a new bar is inside of: the most recently started one that is still running"""
        for bar in sorted(self._bars.values(), key=lambda b: b.id, reverse=True):
            if not bar.done:
                return bar
        return None

    def _root(self, include_finished: bool = False) -> Optional[_Bar]:
        """The bar that is the progress of the run: the pinned one, else the outermost bar that is running"""
        active = [b for b in self._bars.values() if not (b.closed if include_finished else b.done)]
        if self._pinned:
            matching = [b for b in active if b.label.lower() == self._pinned]
            return min(matching, key=lambda b: (b.depth(), -b.id)) if matching else None
        roots = [b for b in active if b.parent is None]
        return max(roots, key=lambda b: b.id) if roots else None

    @staticmethod
    def _inner(bar: _Bar) -> Optional[_Bar]:
        active = [c for c in bar.children if not c.done]
        return max(active, key=lambda c: c.id) if active else None

    def _fraction(self, bar: _Bar) -> Optional[float]:
        total = bar.total
        if total is None:
            return None
        inner = self._inner(bar)
        extra = (self._fraction(inner) or 0.0) if inner is not None else 0.0
        return max(0.0, min(1.0, (bar.n + extra) / total))

    def _chain(self, root: _Bar) -> List[_Bar]:
        chain = [root]
        while len(chain) < MAX_LEVELS and self._inner(chain[-1]) is not None:
            chain.append(self._inner(chain[-1]))
        return chain

    def _report(self, root: _Bar) -> Dict[str, Any]:
        chain = self._chain(root)
        fraction = self._fraction(root)
        percent = None
        if fraction is not None:
            if root.id != self._last_root:
                self._last_root, self._best = root.id, 0
            self._best = max(self._best, int(fraction * 100))
            percent = self._best
        return {
            "percent": percent,
            "bars": [
                {
                    "label": bar.label,
                    "n": int(bar.n) if bar.n is not None else None,
                    "total": int(bar.total) if bar.total is not None else None,
                }
                for bar in chain
            ],
        }

    def state(self) -> Optional[Dict[str, Any]]:
        """
        What to report now: the percentage of the run and the bars (outermost first). While no bar that counts is
        running it is the idle state (no bars). None as long as no bar has ever been started: nothing to report.
        """
        with self._lock:
            self.changed = False
            root = self._root()
            state = self._report(root) if root is not None else (IDLE if self._started_any else None)
            self._prune()
            return dict(state) if state is not None else None

    def _prune(self) -> None:
        """Forget bars that are over and have nothing running inside them"""
        for bar in [b for b in self._bars.values() if b.done and all(c.done for c in b.children)]:
            del self._bars[bar.id]
            if bar.parent is not None and bar in bar.parent.children:
                bar.parent.children.remove(bar)


IDLE: Dict[str, Any] = {"percent": None, "bars": []}

tracker = ProgressTracker()


class ProgressReporter(threading.Thread):
    """Sends the tracker's state to the server when it changed, at most every ``SEND_EVERY`` seconds"""

    def __init__(self, task: "Task", progress_tracker: ProgressTracker = tracker, send_every: float = SEND_EVERY):
        super().__init__(name="cairnml-progress", daemon=True)
        self._task = task
        self._tracker = progress_tracker
        self._send_every = send_every
        self._pid = os.getpid()
        self._stop_event = threading.Event()
        self._sent: Optional[Dict[str, Any]] = None
        self._last_sent_at = 0.0
        self._failures = 0

    def run(self) -> None:
        while not self._stop_event.wait(CHECK_EVERY):
            if os.getpid() != self._pid:  # a forked copy of the process leaves the reporting to the original
                return
            if self._tracker.changed:
                self.report()

    def report(self, final: bool = False) -> None:
        if self._failures >= GIVE_UP_AFTER:
            return
        now = time.monotonic()
        if not final and now - self._last_sent_at < self._send_every:
            return
        state = self._tracker.state()
        if state is None or state == self._sent:
            return
        self._last_sent_at = now  # a failed report is not repeated at once either
        if self._send(state):
            self._sent = state

    def _send(self, state: Dict[str, Any]) -> bool:
        """One attempt, no retries: the next report comes soon enough, and it must never hold up the run or flood"""
        try:
            from ..backend_api.services import tasks  # noqa

            result = self._task.session.send(
                tasks.UpdateProgressRequest(task=self._task.id, percent=state["percent"], bars=state["bars"])
            )
            ok = bool(result is not None and result.meta.result_code == 200)
        except Exception:  # noqa
            ok = False
        self._failures = 0 if ok else self._failures + 1
        return ok

    def stop(self, final: bool = True) -> None:
        self._stop_event.set()
        if final and os.getpid() == self._pid:
            self.report(final=True)


_reporter: Optional[ProgressReporter] = None


def attach(task: "Task", pinned: Optional[str] = None) -> None:
    """Start reporting the progress of this process to the task (once; offline runs and old servers are skipped)"""
    global _reporter
    tracker.set_pinned(pinned)
    if _reporter is not None or task is None:
        return
    try:
        from ..backend_api import Session

        if task.is_offline() or not Session.check_min_api_version("2.23"):
            return
    except Exception:  # noqa
        return
    _reporter = ProgressReporter(task)
    _reporter.start()


def detach() -> None:
    """Send the last state and stop; warn if the bar that was asked for never started"""
    global _reporter
    if tracker.pinned and not tracker.pinned_seen:
        from ..debugging.log import LoggerRoot

        LoggerRoot.get_base_logger().warning(
            f"No progress bar named '{tracker.pinned}' was started, so no progress was reported. "
            "Check Task.init(progress_bar=...) against the label or description of your bar"
        )
    if _reporter is not None:
        _reporter.stop(final=True)
        _reporter = None


class ProgressBar:
    """
    A progress bar for a loop, like ``tqdm``, that is reported as the progress of the run:

    .. code-block:: py

        for epoch in Task.progress(range(10), label="epochs"):
            for batch in Task.progress(train_loader, label="batches"):
                ...

    Bars inside bars are fractions of the outer one. It also works as a context manager with ``update()``.
    """

    def __init__(self, iterable: Optional[Iterable] = None, label: Optional[str] = None, total: Optional[int] = None):
        if total is None and iterable is not None:
            try:
                total = len(iterable)  # type: ignore[arg-type]
            except TypeError:
                total = None
        self._iterable = iterable
        self._bar = tracker.start(label=label, total=total)
        self._count = 0

    def __iter__(self) -> Iterator[Any]:
        try:
            for item in self._iterable or ():
                yield item
                self._count += 1
                tracker.update(self._bar, self._count)
        finally:
            self.close()

    def __len__(self) -> int:
        return int(self._bar.total or 0)

    def __enter__(self) -> "ProgressBar":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def update(self, n: int = 1) -> None:
        """Count ``n`` more steps done"""
        self._count += n
        tracker.update(self._bar, self._count)

    def set_total(self, total: int) -> None:
        tracker.update(self._bar, self._count, total)

    def close(self) -> None:
        tracker.end(self._bar)


class PatchTqdm:
    """Registers every tqdm bar of the script with the tracker, so that nothing has to be changed in the script"""

    __patched = False

    @classmethod
    def patch(cls) -> None:
        if cls.__patched:
            return
        try:
            from tqdm import std
        except ImportError:
            return
        cls.__patched = True
        base = std.tqdm
        original_init, original_display, original_close = base.__init__, base.display, base.close

        def init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, **kwargs)
            try:
                if not getattr(self, "disable", False):
                    self._cairnml_bar = tracker.start(label=getattr(self, "desc", None), total=self.total, source=self)
            except Exception:  # noqa
                pass

        def display(self: Any, *args: Any, **kwargs: Any) -> Any:
            result = original_display(self, *args, **kwargs)
            tracker.touch(getattr(self, "_cairnml_bar", None))
            return result

        def close(self: Any) -> Any:
            bar = getattr(self, "_cairnml_bar", None)
            if bar is not None:
                # the bar is over for good: remember how far it got before tqdm resets its fields
                tracker.update(bar, self.n, self.total)
                bar._source = None
            try:
                return original_close(self)
            finally:
                tracker.end(bar)

        base.__init__, base.display, base.close = init, display, close

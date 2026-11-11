import threading
import time

from cairnml.utilities.lowlevel.threads import kill_thread


def test_a_killed_thread_ends_quietly_and_is_not_seen_as_an_ordinary_error():
    """
    The package analysis of a crashed run is stopped with kill_thread. The analysis catches Exception to log
    "Failed auto-generating package requirements": the kill must end the thread with SystemExit and never reach that
    handler (it did, as a SystemError, when the exception was passed as an instance).
    """
    caught = []
    started = threading.Event()

    def work():
        try:
            started.set()
            while True:
                time.sleep(0.01)
        except Exception as ex:  # noqa: BLE001
            caught.append(ex)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    assert started.wait(2)
    assert kill_thread(thread, wait=True)
    thread.join(2)
    assert not thread.is_alive()
    assert caught == []

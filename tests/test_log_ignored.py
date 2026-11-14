"""An error the SDK swallows is logged at debug level with its traceback, and execution continues."""

import logging

from cairnml.debugging.ignored import log_ignored


def test_a_swallowed_error_is_logged_at_debug_with_its_traceback(caplog):
    logger = logging.getLogger("cairnml.test.log_ignored")
    caplog.set_level(logging.DEBUG, logger=logger.name)

    def read_setting():
        try:
            raise ValueError("boom")
        except Exception:
            log_ignored(logger, "reading the setting")
            return None

    assert read_setting() is None

    records = [r for r in caplog.records if r.name == logger.name]
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    assert "reading the setting" in records[0].getMessage()
    assert records[0].exc_info is not None
    assert records[0].exc_info[0] is ValueError


def test_the_loop_goes_on_after_a_logged_error():
    logger = logging.getLogger("cairnml.test.log_ignored.loop")
    results = []
    for value in (1, 0, 2):
        try:
            results.append(1 / value)
        except ZeroDivisionError:
            log_ignored(logger, "dividing")
    assert results == [1.0, 0.5]

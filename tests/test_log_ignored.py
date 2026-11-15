"""An error the SDK swallows is logged at debug level with its traceback, and execution continues."""

import logging

from cairnml.debugging.ignored import log_ignored


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_a_swallowed_error_is_logged_at_debug_with_its_traceback():
    # the cairnml logger does not propagate to the root logger, so caplog cannot see its records: attach to this logger
    logger = logging.getLogger("cairnml.test.log_ignored")
    records_handler = _Records()
    logger.addHandler(records_handler)
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        _check_swallowed_error_is_logged(logger, records_handler.records)
    finally:
        logger.removeHandler(records_handler)
        logger.setLevel(previous_level)


def _check_swallowed_error_is_logged(logger, captured):

    def read_setting():
        try:
            raise ValueError("boom")
        except Exception:
            log_ignored(logger, "reading the setting")
            return None

    assert read_setting() is None

    records = [r for r in captured if r.name == logger.name]
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

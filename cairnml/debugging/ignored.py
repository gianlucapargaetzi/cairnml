"""Logging for an error the SDK deliberately swallows."""

import logging


def log_ignored(logger: logging.Logger, where: str) -> None:
    """Log the exception being handled at debug level, with its traceback, so a failure stays visible in debug output.

    Call it inside the ``except`` block, with ``where`` naming the place the error was swallowed. Execution then continues
    as before: the error is not raised.
    """
    logger.debug("ignored an error in %s", where, exc_info=True)

"""Running the command line tools the dataset workflow needs (git, dvc, gh), with a time limit.

A network call that stalls must end with an error that says what was running, not hang a training job forever.
Short commands get ``CAIRNML_COMMAND_TIMEOUT_SEC`` (default 300). Commands that move data (a dvc push or pull, a
clone of an external source) are not limited by default, because their time depends on the size of the data;
set ``CAIRNML_TRANSFER_TIMEOUT_SEC`` to limit them too.
"""

import os
import subprocess
from typing import Dict, Optional, Sequence

from .errors import DatasetError

COMMAND_TIMEOUT_ENV = "CAIRNML_COMMAND_TIMEOUT_SEC"
TRANSFER_TIMEOUT_ENV = "CAIRNML_TRANSFER_TIMEOUT_SEC"
DEFAULT_COMMAND_TIMEOUT_SEC = 300.0


class CommandTimeout(DatasetError):
    pass


def _seconds(name: str, default: Optional[float]) -> Optional[float]:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        seconds = float(value)
    except ValueError:
        raise DatasetError(f"{name} must be a number of seconds, not {value!r}") from None
    return seconds if seconds > 0 else None  # 0 means no limit


def command_timeout() -> Optional[float]:
    return _seconds(COMMAND_TIMEOUT_ENV, DEFAULT_COMMAND_TIMEOUT_SEC)


def transfer_timeout() -> Optional[float]:
    return _seconds(TRANSFER_TIMEOUT_ENV, None)


def run(
    args: Sequence[str],
    *,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = -1,
    text: bool = True,
) -> "subprocess.CompletedProcess":
    """Run a command and capture its output. ``timeout=-1`` (the default) means the short-command limit;
    ``None`` means no limit. Raises :class:`CommandTimeout` when the limit is reached, and lets
    ``FileNotFoundError`` through so the caller can say which tool is missing."""
    limit = command_timeout() if timeout == -1 else timeout
    try:
        return subprocess.run(
            list(args), cwd=cwd, env=env, capture_output=True, text=text, check=False, timeout=limit
        )
    except subprocess.TimeoutExpired:
        raise CommandTimeout(
            f"{os.path.basename(args[0])} {' '.join(args[1:3])} did not finish within {limit:.0f} s. "
            f"Check the network, or raise the limit with {COMMAND_TIMEOUT_ENV} (or {TRANSFER_TIMEOUT_ENV} for large transfers)."
        ) from None

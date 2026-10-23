"""Reads the job's token file. The agent keeps the job's current token there and renews it before it expires, so the
job's session reads the file again when it refreshes its token."""

from pathlib import Path
from typing import Optional


def read_job_token_file(path) -> Optional[str]:
    """The token in the file, or None when no file is named, it cannot be read, or it is empty."""
    if not path:
        return None
    try:
        token = Path(path).read_text().strip()
    except OSError:
        return None
    return token or None

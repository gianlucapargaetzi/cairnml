import sys
from os import getenv, environ
from pathlib2 import Path
from typing import Callable, Union, Dict, Any

from .converters import text_to_bool
from .entry import Entry, NotSet


_warned_legacy_keys = set()


def warn_deprecated_once(key: str, message: str) -> None:
    """Print a deprecation warning to stderr, once per process for each key."""
    if key in _warned_legacy_keys:
        return
    _warned_legacy_keys.add(key)
    print(f"warning: {message}", file=sys.stderr)


def check_legacy_config_file(path: str) -> None:
    """Warn once when the ClearML-era configuration file `trains.conf` exists and is read."""
    if Path(path).name == "trains.conf" and Path(path).is_file():
        warn_deprecated_once(
            "trains.conf",
            f"the configuration file {path} is deprecated and will be removed; rename it to cairnml.conf",
        )


class EnvEntry(Entry):
    @classmethod
    def default_conversions(cls) -> Dict[Any, Callable[[str], Any]]:
        conversions = super(EnvEntry, cls).default_conversions().copy()
        conversions[bool] = text_to_bool
        return conversions

    def __init__(self, key: str, *more_keys: Any, **kwargs: Any) -> None:
        super(EnvEntry, self).__init__(key, *more_keys, **kwargs)
        self._ignore_errors = kwargs.pop("ignore_errors", False)

    def pop(self) -> None:
        for k in self.keys:
            environ.pop(k, None)

    def _get(self, key: str) -> Union[str, Any]:
        value = getenv(key, "").strip()
        return value or NotSet

    def _set(self, key: str, value: str) -> None:
        environ[key] = value

    def __str__(self) -> str:
        return f"env:{super(EnvEntry, self).__str__()}"

    def error(self, message: str) -> None:
        if not self._ignore_errors:
            print(f"Environment configuration: {message}")

    def exists(self) -> bool:
        return any(key for key in self.keys if getenv(key) is not None)

import sys
from os import getenv, environ

from .converters import text_to_bool
from .entry import Entry, NotSet

_warned_legacy_keys = set()


class EnvEntry(Entry):
    def default_conversions(self):
        conversions = super(EnvEntry, self).default_conversions().copy()
        conversions[bool] = lambda x: text_to_bool(x.strip())
        return conversions

    def pop(self):
        for k in self.keys:
            environ.pop(k, None)

    def _get(self, key):
        value = getenv(key, "")
        if value and key != self.key and key not in _warned_legacy_keys:
            # Old (ClearML-era) names still work, but each one is reported once per process.
            _warned_legacy_keys.add(key)
            print(
                "warning: the environment variable {} is deprecated and will be removed; use {} instead".format(
                    key, self.key),
                file=sys.stderr,
            )
        return value or NotSet

    def _set(self, key, value):
        environ[key] = value

    def __str__(self):
        return "env:{}".format(super(EnvEntry, self).__str__())

    def error(self, message):
        print("Environment configuration: {}".format(message))

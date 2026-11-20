"""The agent's configuration: the SDK's Config, with the agent's own files, its CAIRNML_AGENT__ variables and the
behaviour its code relies on.

Reading, merging and resolving the configuration is the SDK's. This module only supplies what differs for the agent:
the local files it reads (defs.LOCAL_CONFIG_FILES), the variables that override the configuration, and the
conventions the agent's code was written against (put, pop, as_plain_ordered_dict, attribute access into the tree,
square brackets that give None for a missing key, get that gives the default or the sentinel rather than an error,
debug mode in initialize_logging). The characterisation tests in tests/test_backend_config_characterisation.py pin
each of these.
"""

import functools
import os
from os.path import expanduser

from cairnml.backend_config.config import Config as SdkConfig
from cairnml.backend_config.environment import warn_deprecated_once
from pyhocon import ConfigException, ConfigFactory, ConfigTree

from . import defs
from .defs import LOCAL_CONFIG_FILE_OVERRIDE_VAR


class Config(SdkConfig):
    # the prefix of the variables that override any setting: CAIRNML_AGENT__api__http__retries=3 sets api.http.retries
    extra_config_values_env_key_prefix = "CAIRNML_AGENT__"
    extra_config_values_env_key_sep = "__"

    def __init__(
        self,
        config_folder=None,
        env=None,
        verbose=True,
        relative_to=None,
        app=None,
        is_server=False,
        **kwargs,
    ):
        super().__init__(
            config_folder=config_folder,
            env=env,
            verbose=verbose,
            relative_to=relative_to,
            app=app,
            is_server=is_server,
            **kwargs,
        )

    def local_config_files(self):
        """The agent's own files; the override variable replaces them, as it does for the SDK."""
        local_config_override = LOCAL_CONFIG_FILE_OVERRIDE_VAR.get()
        if local_config_override:
            print(f"Using custom configuration file from env var: {local_config_override}")
            return [expanduser(local_config_override)]
        files = list(defs.LOCAL_CONFIG_FILES)
        self._warn_about_legacy_file(files)
        return files

    @staticmethod
    def _warn_about_legacy_file(files):
        """Say once that ~/trains.conf, a name from the ClearML-based original, is read, and which file replaces it.

        The SDK warns about the same file, but it tells the user to rename it to cairnml.conf, which the agent does not
        read. The warning is given under the SDK's key ("trains.conf"), so the SDK's own warning stays quiet.
        """
        for path in files:
            path = expanduser(path)
            if os.path.basename(path) == "trains.conf" and os.path.isfile(path):
                warn_deprecated_once(
                    "trains.conf",
                    f"the configuration file {path} is deprecated and will be removed; "
                    f"move its settings to {expanduser(files[-1])}",
                )

    def extra_env_config_values(self):
        """The settings given as CAIRNML_AGENT__<path>__<key> variables (read in name order, later ones win)."""
        result = ConfigTree()
        prefix = self.extra_config_values_env_key_prefix
        for key in sorted(k for k in os.environ if k.startswith(prefix)):
            path = key[len(prefix) :].replace(self.extra_config_values_env_key_sep, ".").lower()
            result = ConfigTree.merge_configs(result, ConfigFactory.parse_string(f"{path}: {os.environ[key]}"))
        return result

    def resolve_override_configs(self, initial=None):
        """The overrides merged onto initial, or initial itself (None by default) when there are none."""
        if not self._overrides_configs:
            return initial
        return functools.reduce(
            lambda cfg, override: ConfigTree.merge_configs(cfg, override, copy_trees=True),
            self._overrides_configs,
            initial or ConfigTree(),
        )

    def put(self, key, value):
        self._config.put(key, value)

    def pop(self, key, default=None):
        return self._config.pop(key, default=default)

    def as_plain_ordered_dict(self):
        return self._config.as_plain_ordered_dict()

    def get(self, key, default=SdkConfig._MISSING):
        """The value of key, or default when it is missing. Without a default a missing key gives the sentinel."""
        return self._config.get(key, default)

    def __getitem__(self, key):
        try:
            return self._config[key]
        except ConfigException:
            return None

    def __getattr__(self, key):
        """Attribute access reaches the tree: config.agent is the agent subtree, config.copy() the tree's copy."""
        tree = self.__getattribute__("_config")
        if key.split(".")[0] in tree:
            try:
                return tree[key]
            except ConfigException:
                return None
        return getattr(tree, key)

    def initialize_logging(self, debug=False):
        """Sets the level of every logger and the root to DEBUG when debug is set, then initialises the logging."""
        logging_config = self._config.get("logging", None)
        if debug and logging_config:
            root_logger = logging_config.get("root", None)
            for logger_config in list(logging_config.get("loggers", {}).values()) + (
                [root_logger] if root_logger else []
            ):
                logger_config["level"] = "DEBUG"
        return super().initialize_logging()

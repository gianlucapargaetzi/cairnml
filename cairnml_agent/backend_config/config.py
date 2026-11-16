"""The agent's configuration: the SDK's, with the agent's own files and its CAIRNML_AGENT__ environment overrides."""

import os

from pyhocon import ConfigFactory, ConfigTree

from cairnml.backend_config.config import Config as SdkConfig
from cairnml.backend_config.defs import LOCAL_CONFIG_FILE_OVERRIDE_VAR
from . import defs


class Config(SdkConfig):
    extra_config_values_env_key_sep = "__"
    extra_config_values_env_key_prefix = ["CAIRNML_AGENT" + extra_config_values_env_key_sep]

    def local_config_files(self):
        override = LOCAL_CONFIG_FILE_OVERRIDE_VAR.get()
        if override:
            return [os.path.expanduser(override)]
        return list(defs.LOCAL_CONFIG_FILES)

    def extra_env_config_values(self):
        """Values from CAIRNML_AGENT__<path>__<key> environment variables, for example CAIRNML_AGENT__api__http__retries=3."""
        result = ConfigTree()
        for prefix in self.extra_config_values_env_key_prefix:
            keys = sorted(k for k in os.environ if k.startswith(prefix))
            for key in keys:
                path = key[len(prefix) :].replace(self.extra_config_values_env_key_sep, ".").lower()
                result = ConfigTree.merge_configs(
                    result, ConfigFactory.parse_string("{}: {}".format(path, os.environ[key]))
                )
        return result

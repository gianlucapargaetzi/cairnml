from os.path import expanduser

from cairnml.backend_config.defs import (  # noqa: F401  # the SDK's definitions, the same in the agent
    CONFIG_FILE_EXTENSION,
    DEFAULT_CONFIG_FOLDER,
    ENV_CONFIG_PATHS,
    ENV_VAR,
    LOCAL_CONFIG_PATHS,
    Environment,
    is_config_file,
)

from ..backend_config.environment import EnvEntry

LOCAL_CONFIG_FILES = [
    expanduser("~/trains.conf"),  # the ClearML-era name, still read; Config warns about it and names the file to use
    expanduser("~/cairnml-agent.conf"),  # the earlier location, still read so that existing installs keep working
    expanduser(
        "~/.cairnml/agent/cairnml-agent.conf"
    ),  # written by cairnml-agent init, next to the device key (last = preferred)
]
""" Local config files of the agent (not paths). The agent does not read the CairnML SDK's ~/cairnml.conf """


LOCAL_CONFIG_FILE_OVERRIDE_VAR = EnvEntry("CAIRNML_CONFIG_FILE")
""" Local config file override environment variable. If this is set, no other local config files will be used. """


ENV_CONFIG_PATH_OVERRIDE_VAR = EnvEntry("CAIRNML_CONFIG_PATH")
"""
Environment-related config path override environment variable. If this is set, no other env config path will be used.
"""

ENV_CONFIG_VERBOSE = EnvEntry("CAIRNML_CONFIG_VERBOSE", default=False, converter=bool)

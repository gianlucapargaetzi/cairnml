"""The agent's configuration settings: the SDK's, plus the agent's own files and its own settings."""

from os.path import expanduser

from cairnml.backend_config.defs import (  # noqa: F401  # re-exported for the agent's modules
    CONFIG_VERBOSE,
    DEFAULT_CONFIG_FOLDER,
    ENV_CONFIG_PATH_OVERRIDE_VAR,
    ENV_CONFIG_PATHS,
    ENV_VAR,
    LOCAL_CONFIG_FILE_OVERRIDE_VAR,
    LOCAL_CONFIG_PATHS,
    Environment,
)

ENV_CONFIG_VERBOSE = CONFIG_VERBOSE

LOCAL_CONFIG_FILES = [
    expanduser("~/trains.conf"),  # used for workstation configuration (end-users, workers)
    expanduser("~/cairnml-agent.conf"),  # the earlier location, still read so that existing installs keep working
    expanduser(
        "~/.cairnml/agent/cairnml-agent.conf"
    ),  # written by cairnml-agent init, next to the device key (last = preferred)
]
""" Local config files of the agent (not paths). The agent does not read the CairnML SDK's ~/cairnml.conf """


class UptimeConf(object):
    queue_tag_on = "force_workers:on"
    queue_tag_off = "force_workers:off"
    worker_key = "force"
    worker_value_off = ["off"]
    worker_value_on = ["on"]

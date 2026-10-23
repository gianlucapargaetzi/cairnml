from typing import Optional

from ...backend_config import EnvEntry
from ...backend_config.converters import safe_text_to_bool

ENV_HOST = EnvEntry("CAIRNML_API_HOST", "TRAINS_API_HOST")
ENV_WEB_HOST = EnvEntry("CAIRNML_WEB_HOST", "TRAINS_WEB_HOST")
ENV_AUTH_TOKEN = EnvEntry("CAIRNML_AUTH_TOKEN")
# Set by the agent for one job: the token is used as it is and is never exchanged for another.
ENV_JOB_TOKEN = EnvEntry("CAIRNML_JOB_TOKEN")
# The file the agent keeps the job's current token in, renewed while the job runs; read again on every refresh.
ENV_JOB_TOKEN_FILE = EnvEntry("CAIRNML_JOB_TOKEN_FILE")
ENV_VERBOSE = EnvEntry(
    "CAIRNML_API_VERBOSE",
    "TRAINS_API_VERBOSE",
    converter=safe_text_to_bool,
    type=bool,
    default=False,
)
ENV_HOST_VERIFY_CERT = EnvEntry("CAIRNML_API_HOST_VERIFY_CERT", "TRAINS_API_HOST_VERIFY_CERT", default=True)
ENV_OFFLINE_MODE = EnvEntry(
    "CAIRNML_OFFLINE_MODE",
    "TRAINS_OFFLINE_MODE",
    type=bool,
    converter=safe_text_to_bool,
)
ENV_CAIRNML_NO_DEFAULT_SERVER = EnvEntry(
    "CAIRNML_NO_DEFAULT_SERVER",
    "TRAINS_NO_DEFAULT_SERVER",
    converter=safe_text_to_bool,
    type=bool,
    default=True,
)
ENV_DISABLE_VAULT_SUPPORT = EnvEntry("CAIRNML_DISABLE_VAULT_SUPPORT", type=bool)
ENV_ENABLE_ENV_CONFIG_SECTION = EnvEntry("CAIRNML_ENABLE_ENV_CONFIG_SECTION", type=bool)
ENV_ENABLE_FILES_CONFIG_SECTION = EnvEntry("CAIRNML_ENABLE_FILES_CONFIG_SECTION", type=bool)
ENV_DEFERRED_TASK_INIT = EnvEntry("CAIRNML_DEFERRED_TASK_INIT", type=bool)
ENV_IGNORE_MISSING_CONFIG = EnvEntry("CAIRNML_IGNORE_MISSING_CONFIG", type=bool)

"""
Experimental option to set the request method for all API requests and auth login.
This could be useful when GET requests with payloads are blocked by a server as
POST requests can be used instead.

However this has not been vigorously tested and may have unintended consequences.
"""
ENV_API_DEFAULT_REQ_METHOD = EnvEntry("CAIRNML_API_DEFAULT_REQ_METHOD")

"""
Experimental option to make the SDK retry on additional error codes.
Use a comma-separated list of integer return codes.
NOTE: this changes behavior and might cause the experiment to wait
for a very long time for a non-responding or mis-configured server
"""
ENV_API_EXTRA_RETRY_CODES = EnvEntry("CAIRNML_API_EXTRA_RETRY_CODES")

ENV_FORCE_MAX_API_VERSION = EnvEntry("CAIRNML_FORCE_MAX_API_VERSION", type=str)


class MissingConfigError(ValueError):
    def __init__(self, message: Optional[str] = None) -> None:
        if message is None:
            message = (
                "It seems CairnML is not configured on this machine!\n"
                "To get started with CairnML, set up your own 'cairnml-server' "
                "and run 'cairnml-init'.\n"
            )
        super(MissingConfigError, self).__init__(message)

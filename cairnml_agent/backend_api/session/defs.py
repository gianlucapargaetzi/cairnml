from cairnml_agent.helper.environment import EnvEntry
from cairnml_agent.helper.environment.converters import safe_text_to_bool

ENV_HOST = EnvEntry("CAIRNML_API_HOST")
# Set by the agent for one job: the token is used as it is and is never exchanged for another.
ENV_JOB_TOKEN = EnvEntry("CAIRNML_JOB_TOKEN")
# The file the agent keeps the job's current token in, renewed while the job runs; read again on every refresh.
ENV_JOB_TOKEN_FILE = EnvEntry("CAIRNML_JOB_TOKEN_FILE")
ENV_AUTH_TOKEN = EnvEntry("CAIRNML_AUTH_TOKEN")
ENV_CONDA_ENV_PACKAGE = EnvEntry("CAIRNML_CONDA_ENV_PACKAGE")
ENV_USE_CONDA_BASE_ENV = EnvEntry("CAIRNML_USE_CONDA_BASE_ENV", type=bool)
ENV_NO_DEFAULT_SERVER = EnvEntry("CAIRNML_NO_DEFAULT_SERVER", type=bool, default=True)
ENV_ENABLE_ENV_CONFIG_SECTION = EnvEntry("CAIRNML_ENABLE_ENV_CONFIG_SECTION", type=bool)
ENV_ENABLE_FILES_CONFIG_SECTION = EnvEntry("CAIRNML_ENABLE_FILES_CONFIG_SECTION", type=bool)
ENV_VENV_CONFIGURED = EnvEntry("VIRTUAL_ENV", type=str)
ENV_PROPAGATE_EXITCODE = EnvEntry("CAIRNML_AGENT_PROPAGATE_EXITCODE", type=bool, default=False)
ENV_INITIAL_CONNECT_RETRY_OVERRIDE = EnvEntry(
    "CAIRNML_AGENT_INITIAL_CONNECT_RETRY_OVERRIDE", default=True, converter=safe_text_to_bool
)
# values are 0/None (task per node), 1/2 (multi-node reporting, colored console), -1 (only report rank 0 node)
ENV_MULTI_NODE_SINGLE_TASK = EnvEntry("CAIRNML_MULTI_NODE_SINGLE_TASK", type=int, default=None)

ENV_URLLIB3_CONNECT_LEVEL = EnvEntry("CAIRNML_AGENT_URLLIB3_CONNECT_LEVEL", type=str, default="WARNING")
"""
If set, override the urllib3 log level to at least this level what initially connecting to the server.
This is set by default to WARNING to ensure we can detect certificate issues. Level names or numbers can be used.
"""

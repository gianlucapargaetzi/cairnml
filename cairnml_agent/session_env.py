from cairnml_agent.helper.environment.converters import safe_text_to_bool
from cairnml_agent.helper.environment import EnvEntry

# Environment names that only the agent reads (its daemon, conda and job settings); the SDK's are in cairnml.backend_api.session.defs.

ENV_CONDA_ENV_PACKAGE = EnvEntry("CAIRNML_CONDA_ENV_PACKAGE")
ENV_USE_CONDA_BASE_ENV = EnvEntry("CAIRNML_USE_CONDA_BASE_ENV", type=bool)
ENV_NO_DEFAULT_SERVER = EnvEntry("CAIRNML_NO_DEFAULT_SERVER", type=bool, default=True)
ENV_VENV_CONFIGURED = EnvEntry("VIRTUAL_ENV", type=str)
ENV_PROPAGATE_EXITCODE = EnvEntry("CAIRNML_AGENT_PROPAGATE_EXITCODE", type=bool, default=False)
ENV_INITIAL_CONNECT_RETRY_OVERRIDE = EnvEntry(
    "CAIRNML_AGENT_INITIAL_CONNECT_RETRY_OVERRIDE", default=True, converter=safe_text_to_bool
)
ENV_MULTI_NODE_SINGLE_TASK = EnvEntry("CAIRNML_MULTI_NODE_SINGLE_TASK", type=int, default=None)
ENV_URLLIB3_CONNECT_LEVEL = EnvEntry("CAIRNML_AGENT_URLLIB3_CONNECT_LEVEL", type=str, default="WARNING")

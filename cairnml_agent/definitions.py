import shlex
from datetime import timedelta
from enum import IntEnum
from os import getenv, environ
from typing import Text, Optional, Union, Tuple, Any

from ._vendor import six
from ._vendor.pathlib2 import Path

from cairnml_agent.helper.base import normalize_path
from cairnml_agent.helper.environment.converters import strtobool

PROGRAM_NAME = "cairnml-agent"
FROM_FILE_PREFIX_CHARS = "@"

CONFIG_DIR = normalize_path("~/.cairnml")
TOKEN_CACHE_FILE = normalize_path("~/.cairnml.cairnml_agent.tmp")

CONFIG_FILE_CANDIDATES = ["~/cairnml.conf"]


def find_config_path():
    for candidate in CONFIG_FILE_CANDIDATES:
        if Path(candidate).expanduser().exists():
            return candidate
    return CONFIG_FILE_CANDIDATES[0]


CONFIG_FILE = normalize_path(find_config_path())


class EnvironmentConfig(object):

    conversions = {
        bool: lambda value: bool(strtobool(value)),
        six.text_type: lambda s: six.text_type(s).strip(),
        list: lambda s: shlex.split(s.strip()),
    }

    def __init__(self, *names, **kwargs):
        self.vars = names
        self.type = kwargs.pop("type", six.text_type)

    def pop(self):
        for k in self.vars:
            environ.pop(k, None)

    def set(self, value):
        for k in self.vars:
            environ[k] = str(value)

    def convert(self, value):
        return self.conversions.get(self.type, self.type)(value)

    def get(self, key=False):  # type: (bool) -> Optional[Union[Any, Tuple[Text, Any]]]
        for name in self.vars:
            value = getenv(name)
            if value:
                value = self.convert(value)
                if key:
                    return name, value
                return value
        return None


ENV_AGENT_AUTH_TOKEN = EnvironmentConfig("CAIRNML_AUTH_TOKEN")
ENV_AWS_SECRET_KEY = EnvironmentConfig("AWS_SECRET_ACCESS_KEY")
ENV_AZURE_ACCOUNT_KEY = EnvironmentConfig("AZURE_STORAGE_KEY")

ENVIRONMENT_CONFIG = {
    "api.api_server": EnvironmentConfig(
        "CAIRNML_API_HOST",
        "TRAINS_API_HOST",
    ),
    "api.web_server": EnvironmentConfig(
        "CAIRNML_WEB_HOST",
        "TRAINS_WEB_HOST",
    ),
    "agent.worker_name": EnvironmentConfig(
        "CAIRNML_WORKER_NAME",
        "TRAINS_WORKER_NAME",
    ),
    "agent.worker_id": EnvironmentConfig(
        "CAIRNML_WORKER_ID",
        "TRAINS_WORKER_ID",
    ),
    "agent.cuda_version": EnvironmentConfig("CAIRNML_CUDA_VERSION", "TRAINS_CUDA_VERSION", "CUDA_VERSION"),
    "agent.cudnn_version": EnvironmentConfig("CAIRNML_CUDNN_VERSION", "TRAINS_CUDNN_VERSION", "CUDNN_VERSION"),
    "agent.cpu_only": EnvironmentConfig(names=("CAIRNML_CPU_ONLY", "TRAINS_CPU_ONLY", "CPU_ONLY"), type=bool),
    "agent.crash_on_exception": EnvironmentConfig("CAIRNML_AGENT_CRASH_ON_EXCEPTION", type=bool),
    "sdk.aws.s3.key": EnvironmentConfig("AWS_ACCESS_KEY_ID"),
    "sdk.aws.s3.secret": ENV_AWS_SECRET_KEY,
    "sdk.aws.s3.region": EnvironmentConfig("AWS_DEFAULT_REGION"),
    "sdk.azure.storage.containers.0": {
        "account_name": EnvironmentConfig("AZURE_STORAGE_ACCOUNT"),
        "account_key": ENV_AZURE_ACCOUNT_KEY,
    },
    "sdk.google.storage.credentials_json": EnvironmentConfig("GOOGLE_APPLICATION_CREDENTIALS"),
}

ENVIRONMENT_SDK_PARAMS = {
    "task_id": (
        "CAIRNML_TASK_ID",
        "TRAINS_TASK_ID",
    ),
    "config_file": (
        "CAIRNML_CONFIG_FILE",
        "TRAINS_CONFIG_FILE",
    ),
    "log_level": (
        "CAIRNML_LOG_LEVEL",
        "TRAINS_LOG_LEVEL",
    ),
    "log_to_backend": (
        "CAIRNML_LOG_TASK_TO_BACKEND",
        "TRAINS_LOG_TASK_TO_BACKEND",
    ),
}

ENVIRONMENT_BACKWARD_COMPATIBLE = EnvironmentConfig(names=("CAIRNML_AGENT_ALG_ENV", "TRAINS_AGENT_ALG_ENV"), type=bool)

VIRTUAL_ENVIRONMENT_PATH = {
    "python2": normalize_path(CONFIG_DIR, "py2venv"),
    "python3": normalize_path(CONFIG_DIR, "py3venv"),
}

DEFAULT_BASE_DIR = normalize_path(CONFIG_DIR, "data_cache")
DEFAULT_HOST = "http://localhost/cairnml/api"
MAX_DATASET_SOURCES_COUNT = 50000

INVALID_WORKER_ID = (400, 1001)
WORKER_ALREADY_REGISTERED = (400, 1003)

API_VERSION = "v1.5"
TOKEN_EXPIRATION_SECONDS = int(timedelta(days=2).total_seconds())

METADATA_EXTENSION = ".json"

WORKING_REPOSITORY_DIR = "task_repository"
WORKING_STANDALONE_DIR = "code"
DEFAULT_VCS_CACHE = normalize_path(CONFIG_DIR, "vcs-cache")
PIP_EXTRA_INDICES = []
DEFAULT_PIP_DOWNLOAD_CACHE = normalize_path(CONFIG_DIR, "pip-download-cache")
ENV_PIP_EXTRA_INSTALL_FLAGS = EnvironmentConfig("CAIRNML_EXTRA_PIP_INSTALL_FLAGS", type=list)
ENV_DOCKER_IMAGE = EnvironmentConfig("CAIRNML_DOCKER_IMAGE", "TRAINS_DOCKER_IMAGE")
ENV_WORKER_ID = EnvironmentConfig("CAIRNML_WORKER_ID", "TRAINS_WORKER_ID")
ENV_WORKER_TAGS = EnvironmentConfig("CAIRNML_WORKER_TAGS")
ENV_AGENT_SKIP_PIP_VENV_INSTALL = EnvironmentConfig("CAIRNML_AGENT_SKIP_PIP_VENV_INSTALL")
ENV_AGENT_SKIP_PYTHON_ENV_INSTALL = EnvironmentConfig("CAIRNML_AGENT_SKIP_PYTHON_ENV_INSTALL", type=bool)
ENV_AGENT_FORCE_CODE_DIR = EnvironmentConfig("CAIRNML_AGENT_FORCE_CODE_DIR")
ENV_AGENT_FORCE_EXEC_SCRIPT = EnvironmentConfig("CAIRNML_AGENT_FORCE_EXEC_SCRIPT")
ENV_AGENT_FORCE_POETRY = EnvironmentConfig("CAIRNML_AGENT_FORCE_POETRY", type=bool)
ENV_AGENT_FORCE_UV = EnvironmentConfig("CAIRNML_AGENT_FORCE_UV", type=bool)
ENV_BOOTSTRAP_UV_EXEC = EnvironmentConfig("CAIRNML_BOOTSTRAP_UV_EXEC", type=str)
ENV_AGENT_FORCE_TASK_INIT = EnvironmentConfig("CAIRNML_AGENT_FORCE_TASK_INIT", type=bool)
ENV_DOCKER_SKIP_GPUS_FLAG = EnvironmentConfig("CAIRNML_DOCKER_SKIP_GPUS_FLAG", "TRAINS_DOCKER_SKIP_GPUS_FLAG")
ENV_AGENT_GIT_USER = EnvironmentConfig("CAIRNML_AGENT_GIT_USER", "TRAINS_AGENT_GIT_USER")
ENV_AGENT_GIT_PASS = EnvironmentConfig("CAIRNML_AGENT_GIT_PASS", "TRAINS_AGENT_GIT_PASS")
ENV_AGENT_GIT_HOST = EnvironmentConfig("CAIRNML_AGENT_GIT_HOST", "TRAINS_AGENT_GIT_HOST")
ENV_AGENT_DISABLE_SSH_MOUNT = EnvironmentConfig("CAIRNML_AGENT_DISABLE_SSH_MOUNT", type=bool)
ENV_AGENT_DEBUG_GET_NEXT_TASK = EnvironmentConfig("CAIRNML_AGENT_DEBUG_GET_NEXT_TASK", type=bool)
ENV_SSH_AUTH_SOCK = EnvironmentConfig("SSH_AUTH_SOCK")
ENV_TASK_EXECUTE_AS_USER = EnvironmentConfig("CAIRNML_AGENT_EXEC_USER", "TRAINS_AGENT_EXEC_USER")
ENV_TASK_EXTRA_PYTHON_PATH = EnvironmentConfig("CAIRNML_AGENT_EXTRA_PYTHON_PATH", "TRAINS_AGENT_EXTRA_PYTHON_PATH")
ENV_DOCKER_HOST_MOUNT = EnvironmentConfig(
    "CAIRNML_AGENT_K8S_HOST_MOUNT",
    "CAIRNML_AGENT_DOCKER_HOST_MOUNT",
    "TRAINS_AGENT_K8S_HOST_MOUNT",
    "TRAINS_AGENT_DOCKER_HOST_MOUNT",
)
ENV_VENV_CACHE_PATH = EnvironmentConfig("CAIRNML_AGENT_VENV_CACHE_PATH")
ENV_EXTRA_DOCKER_ARGS = EnvironmentConfig("CAIRNML_AGENT_EXTRA_DOCKER_ARGS", type=list)
ENV_EXTRA_DOCKER_LABELS = EnvironmentConfig("CAIRNML_AGENT_EXTRA_DOCKER_LABELS", type=list)
ENV_FORCE_HOST_MACHINE_IP = EnvironmentConfig("CAIRNML_AGENT_FORCE_HOST_MACHINE_IP")
ENV_DEBUG_INFO = EnvironmentConfig("CAIRNML_AGENT_DEBUG_INFO")
ENV_CHILD_AGENTS_COUNT_CMD = EnvironmentConfig("CAIRNML_AGENT_CHILD_AGENTS_COUNT_CMD")
ENV_DOCKER_ARGS_FILTERS = EnvironmentConfig("CAIRNML_AGENT_DOCKER_ARGS_FILTERS")
ENV_DOCKER_ARGS_HIDE_ENV = EnvironmentConfig("CAIRNML_AGENT_DOCKER_ARGS_HIDE_ENV")
ENV_CONFIG_BC_IN_STANDALONE = EnvironmentConfig("CAIRNML_AGENT_STANDALONE_CONFIG_BC", type=bool)
ENV_ABORT_CALLBACK_CMD = EnvironmentConfig("CAIRNML_AGENT_ABORT_CALLBACK_CMD")
ENV_ABORT_CALLBACK_CMD_TIMEOUT = EnvironmentConfig("CAIRNML_AGENT_ABORT_CALLBACK_TIMEOUT")

""" Maintain backwards compatible configuration when launching in standalone mode """

ENV_SERVICES_DOCKER_RESTART = EnvironmentConfig("CAIRNML_AGENT_SERVICES_DOCKER_RESTART")
"""
    Specify a restart value for a services agent task containers.
    Note that when a restart value is provided, task containers will not be run with the '--rm' flag and will
     not be cleaned up automatically when completed (this will need to be done externally using the
     'docker container prune' command to free up resources).
    Value format for this env var is "<restart-value>;<task-selector>", where:
    - <restart-value> can be any valid restart value for docker-run (see https://docs.docker.com/engine/reference/commandline/run/#restart)
    - <task-selector> is optional, allowing to restrict this behaviour to specific tasks. The format is:
        "<path-to-task-field>=<value>" where:
        * <path-to-task-field> is a dot-separated path to a task field (e.g. "container.image")
        * <value> is optional. If not provided, the restart policy till be applied for the task container if the
            path provided exists. If provided, the restart policy will be applied if the value matches the value
            obtained from the task (value parsing and comparison is based on the type of value obtained from the task) 
    For example:
        CAIRNML_AGENT_SERVICES_DOCKER_RESTART=unless-stopped
        CAIRNML_AGENT_SERVICES_DOCKER_RESTART=unless-stopped;container.image=some-image
"""

ENV_FORCE_SYSTEM_SITE_PACKAGES = EnvironmentConfig("CAIRNML_AGENT_FORCE_SYSTEM_SITE_PACKAGES", type=bool)
""" Force system_site_packages: true when running tasks in containers (i.e. docker mode or k8s glue) """

ENV_CUSTOM_BUILD_SCRIPT = EnvironmentConfig("CAIRNML_AGENT_CUSTOM_BUILD_SCRIPT")
"""
    Specifies a custom environment setup script to be executed instead of installing a virtual environment.
    If provided, this script is executed following Git cloning. Script command may include environment variable and
    will be expanded before execution (e.g. "$CAIRNML_GIT_ROOT/script.sh").
    The script can also be specified using the `agent.custom_build_script` configuration setting.
    
    When running the script, the following environment variables will be set:
    - CAIRNML_CUSTOM_BUILD_TASK_CONFIG_JSON: specifies a path to a temporary files containing the complete task
     contents in JSON format
    - CAIRNML_TASK_SCRIPT_ENTRY: task entrypoint script as defined in the task's script section
    - CAIRNML_TASK_WORKING_DIR: task working directory as defined in the task's script section
    - CAIRNML_VENV_PATH: path to the agent's default virtual environment path (as defined in the configuration)
    - CAIRNML_GIT_ROOT: path to the cloned Git repository
    - CAIRNML_CUSTOM_BUILD_OUTPUT: a path to a non-existing file that may be created by the script. If created,
     this file must be in the following JSON format:
         ```json
         {
           "binary": "/absolute/path/to/python-executable",
           "entry_point": "/absolute/path/to/task-entrypoint-script",
           "working_dir": "/absolute/path/to/task-working/dir"
         }
         ```
     If provided, the agent will use these instead of the predefined task script section to execute the task and will
     skip virtual environment creation.
    
    In case the custom script returns with a non-zero exit code, the agent will fail with the same exit code.
    In case the custom script is specified but does not exist, or if the custom script does not write valid content
    into the file specified in CAIRNML_CUSTOM_BUILD_OUTPUT, the agent will emit a warning and continue with the
    standard flow.
"""

ENV_PACKAGE_PYTORCH_RESOLVE = EnvironmentConfig("CAIRNML_AGENT_PACKAGE_PYTORCH_RESOLVE")

ENV_TEMP_STDOUT_FILE_DIR = EnvironmentConfig("CAIRNML_AGENT_TEMP_STDOUT_FILE_DIR")

ENV_GIT_CLONE_VERBOSE = EnvironmentConfig("CAIRNML_AGENT_GIT_CLONE_VERBOSE", type=bool)

ENV_GPU_FRACTIONS = EnvironmentConfig("CAIRNML_AGENT_GPU_FRACTIONS")

ENV_QUEUE_POLL_FREQ_SEC = EnvironmentConfig("CAIRNML_AGENT_QUEUE_POLL_FREQ_SEC", type=float)

ENV_STATUS_REPORT_FREQ_SEC = EnvironmentConfig("CAIRNML_AGENT_STATUS_REPORT_FREQ_SEC", type=float)

ENV_SSH_URL_REPLACEMENT_SCHEME = EnvironmentConfig("CAIRNML_AGENT_SSH_URL_REPLACEMENT_SCHEME")

ENV_AGENT_GIT_USE_MS_ENTRA_TOKEN = EnvironmentConfig("CAIRNML_AGENT_GIT_USE_MS_ENTRA_TOKEN", type=bool)
"""Use Azure MS Entra Token for authentication. Azure token will be taken from the git password setting"""

ENV_AGENT_GIT_USE_AZURE_PAT = EnvironmentConfig("CAIRNML_AGENT_GIT_USE_AZURE_PAT", type=bool)
"""Use AzurePersonal Access Token (PAT). Azure PAT will be taken from the git password setting"""


class FileBuffering(IntEnum):
    """
    File buffering options:
    - UNSET: follows the defaults for the type of file,
        line-buffered for interactive (tty) text files and with a default chunk size otherwise
    - UNBUFFERED: no buffering at all
    - LINE_BUFFERED: per-line buffering, only valid for text files
    - values bigger than 1 indicate the size of the buffer in bytes and are not represented by the enum
    """

    UNSET = -1
    UNBUFFERED = 0
    LINE_BUFFERING = 1

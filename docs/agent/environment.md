# Environment variables of the agent

The agent reads its settings from the environment. Every variable has one name, the `CAIRNML_` name; the older `TRAINS_` names are not read. A value set here overrides the configuration file.

"Set for the task" means the agent writes the variable into the environment of a job or of a custom script, so the job can read it. The agent does not read those from its own environment.

The tests in `tests/test_environment_names.py` check that this table lists exactly the `CAIRNML_` names the code reads, and that no `TRAINS_` name is left.

The four settings that were also named `CAIRNML_AGENT_*` are read under their SDK names only: `CAIRNML_CONFIG_VERBOSE`, `CAIRNML_ENABLE_ENV_CONFIG_SECTION`, `CAIRNML_ENABLE_FILES_CONFIG_SECTION`. The `CAIRNML_AGENT_` forms are not read.

## Server connection and sign-in

| Variable | Default | Meaning |
|---|---|---|
| `CAIRNML_API_HOST` | from the configuration | Address of the API, for example `https://example.org/cairnml/api`. |
| `CAIRNML_WEB_HOST` | from the configuration | Address of the web app, for example `https://example.org/cairnml`. |
| `CAIRNML_API_HOST_VERIFY_CERT` | `true` | Verify the server's TLS certificate. Set `false` only for a test server. |
| `CAIRNML_API_VERBOSE` | `false` | Log every API request and response. |
| `CAIRNML_API_DEFAULT_REQ_METHOD` | `GET` | HTTP method for API requests. Experimental: `POST` for a proxy that blocks GET with a body. |
| `CAIRNML_AUTH_TOKEN` | unset | An access token to use instead of signing in. |
| `CAIRNML_NO_DEFAULT_SERVER` | `true` | Do not fall back to a default server address when none is configured. |
| `CAIRNML_API_EXTRA_RETRY_CODES` | unset | Comma-separated HTTP status codes to retry as well, on top of the usual ones. Experimental: it can wait a long time on a server that does not answer. |
| `CAIRNML_AGENT_INITIAL_CONNECT_RETRY_OVERRIDE` | `true` | Override the retry behaviour of the first connection to the server. |
| `CAIRNML_AGENT_URLLIB3_CONNECT_LEVEL` | `WARNING` | Minimum log level of urllib3 messages during the first connection to the server. |
| `CAIRNML_LOGIN_PORT` | `8765` | Local port that receives the browser sign-in reply. |
| `CAIRNML_JOB_TOKEN` | unset | Token of one job, used as it is. Set for the task. |
| `CAIRNML_JOB_TOKEN_FILE` | unset | File that holds the current token of a job; renewed while the job runs. Set for the task. |

SDK switches the agent does not use: the agent ignores `CAIRNML_OFFLINE_MODE`, and `CAIRNML_DEFERRED_TASK_INIT` and `CAIRNML_IGNORE_MISSING_CONFIG` apply to the SDK's task runner, which the agent does not run.

| Variable | Default | Meaning |
|---|---|---|
| `CAIRNML_OFFLINE_MODE` | unset | Ignored by the agent. |
| `CAIRNML_DEFERRED_TASK_INIT` | unset | Ignored by the agent. |
| `CAIRNML_IGNORE_MISSING_CONFIG` | unset | Ignored by the agent. |

## Identity, configuration and environment

| Variable | Default | Meaning |
|---|---|---|
| `CAIRNML_ENV` | `default` | Name of the configuration environment to load. |
| `CAIRNML_CONFIG_FILE` | unset | Configuration file to use instead of the local ones. Set for the task. |
| `CAIRNML_CONFIG_PATH` | unset | Directory of environment configuration; replaces the default search path. |
| `CAIRNML_CONFIG_VERBOSE` | `false` | Log which configuration files are read. |
| `CAIRNML_ENABLE_ENV_CONFIG_SECTION` | unset | Read settings from the environment as a configuration section. |
| `CAIRNML_ENABLE_FILES_CONFIG_SECTION` | unset | Read settings from files as a configuration section. |
| `CAIRNML_WORKER_NAME` | unset | Name of this agent (worker) on the server. |
| `CAIRNML_WORKER_ID` | unset | ID of this agent (worker). Set for the task. |
| `CAIRNML_WORKER_TAGS` | unset | Tags of this agent, separated by spaces. |
| `CAIRNML_AGENT_ALG_ENV` | `false` | Export `ALG_CONFIG_FILE` and `SM_CONFIG_FILE` with the configuration file path, for scripts that expect those names. |
| `CAIRNML_LOG_LEVEL` | unset | Log level of the task's SDK. Set for the task. |
| `CAIRNML_LOG_TASK_TO_BACKEND` | unset | Whether the task's SDK sends its log to the server. Set for the task. |
| `CAIRNML_MULTI_NODE_SINGLE_TASK` | unset | Multi-node reporting: `0` or unset one task per node; `1` or `2` multi-node reporting; `-1` only rank 0 reports. |
| `CAIRNML_SUPPRESS_UPDATE_MESSAGE` | `false` | Do not print the update message of the CairnML package. |
| `CAIRNML_CPU_ONLY` | unset | Treat the machine as having no GPU. |
| `CAIRNML_CUDA_VERSION` | from `CUDA_VERSION` | CUDA version reported by this agent. |
| `CAIRNML_CUDNN_VERSION` | from `CUDNN_VERSION` | cuDNN version reported by this agent. |
| `CAIRNML_AGENT_CRASH_ON_EXCEPTION` | `false` | Stop the agent on an unexpected exception instead of continuing. |
| `CAIRNML_AGENT_PROPAGATE_EXITCODE` | `false` | Exit with the status of the last task instead of `0`. |
| `CAIRNML_AGENT_DEBUG_INFO` | unset | Extra debug information added to the agent's log. |
| `CAIRNML_AGENT_DEBUG_GET_NEXT_TASK` | `false` | Log the details of each request for the next task. |
| `CAIRNML_AGENT_USE_POWERSHELL` | unset | Run the agent's Windows shell commands with PowerShell. |
| `CAIRNML_AGENT_TEMP_STDOUT_FILE_DIR` | unset | Directory for the temporary files that hold a task's output. |
| `CAIRNML_AGENT_QUEUE_POLL_FREQ_SEC` | unset | Seconds between two checks of the queue for a new task. |
| `CAIRNML_AGENT_STATUS_REPORT_FREQ_SEC` | unset | Seconds between two status reports to the server. |
| `CAIRNML_AGENT_ABORT_CALLBACK_CMD` | unset | Command run when a task is aborted. |
| `CAIRNML_AGENT_ABORT_CALLBACK_TIMEOUT` | unset | Timeout in seconds for the abort callback command. |
| `CAIRNML_AGENT_CHILD_AGENTS_COUNT_CMD` | unset | Command that prints the number of child agents running. |
| `CAIRNML_AGENT_CUSTOM_BUILD_SCRIPT` | unset | Script that replaces the agent's default build of a task's environment. |
| `CAIRNML_AGENT_STANDALONE_CONFIG_BC` | unset | In standalone mode, also read the configuration of the backward-compatible files. |
| `CAIRNML_AGENT_SERVICE_TASK` | unset | Set to `1` in a container running a service task. Set for the task. |
| `CAIRNML_AGENT_VERSION` | unset | Version of the agent that started a task. Set for the task. |
| `CAIRNML_AGENT_HOST_IP` | unset | IP address of the host machine. Set for the task when port mappings are used. |
| `CAIRNML_AGENT_FORCE_HOST_MACHINE_IP` | unset | Use this IP address for the host machine instead of the one detected. |

## Tasks and their environments

| Variable | Default | Meaning |
|---|---|---|
| `CAIRNML_TASK_ID` | unset | ID of the task being run. Set for the task. |
| `CAIRNML_CONDA_ENV_PACKAGE` | unset | Conda package that the agent installs into the task's Conda environment. |
| `CAIRNML_USE_CONDA_BASE_ENV` | `false` | Use the base Conda environment for tasks. |
| `CAIRNML_AGENT_FORCE_CODE_DIR` | unset | Directory to run a task's code from, instead of the cloned repository. |
| `CAIRNML_AGENT_FORCE_EXEC_SCRIPT` | unset | Script to execute for a task, instead of the one the task defines. |
| `CAIRNML_AGENT_FORCE_TASK_INIT` | `false` | Run the task's initialization script before the task. |
| `CAIRNML_AGENT_FORCE_POETRY` | `false` | Install the task's packages with Poetry. |
| `CAIRNML_AGENT_FORCE_UV` | `false` | Install the task's packages with uv. |
| `CAIRNML_BOOTSTRAP_UV_EXEC` | unset | Path to the uv executable used for the bootstrap. |
| `CAIRNML_BOOTSTRAP_DIR` | unset | Directory of the bootstrap files, mounted into the container. Set for the task. |
| `CAIRNML_BOOTSTRAP_CACHE_DIR` | unset | Cache directory of the bootstrap, mounted into the container. Set for the task. |
| `CAIRNML_BOOTSTRAP_CUSTOM_CMD_BASE64` | unset | Custom bootstrap command, base64 encoded. Set for the task. |
| `CAIRNML_FORCE_STATIC_GIT_BIN` | `false` | Use the static git binary even when the container has its own git. Set for the bootstrap; the configuration sets it from `agent.bootstrap.always_use_static_git`. |
| `CAIRNML_SKIP_GIT_INST` | `true` | Use the git already in the container when there is one; otherwise use the static binary. Set for the bootstrap from `agent.bootstrap.skip_git_installation`. |
| `CAIRNML_SKIP_CA_CERT_INST` | `true` | Install the ca-certificates package at boot; a failure does not stop the boot. Set for the bootstrap from `agent.bootstrap.always_install_ca_cert`. |
| `CAIRNML_APT_INSTALL` | unset | Extra apt packages the container's bootstrap installs. Set by the bootstrap script; the agent removes it from its own environment. |
| `CAIRNML_PIP_VER` | unset | pip version to install in the container. Set for the bootstrap. |
| `CAIRNML_PYTHON_VER` | unset | Default uv Python version for the container. Set for the bootstrap. |
| `CAIRNML_UPDATE_UV` | unset | Whether the container updates uv. Set for the bootstrap. |
| `CAIRNML_PRE_CUSTOM_CMD` | unset | Shell script run before the task. Set for the task. |
| `CAIRNML_AGENT_SKIP_PIP_VENV_INSTALL` | unset | Do not create a virtual environment for the task; use the global Python interpreter instead. |
| `CAIRNML_AGENT_SKIP_PYTHON_ENV_INSTALL` | `false` | Do not install the Python environment before the task. |
| `CAIRNML_AGENT_SKIP_CONTAINER_APT` | unset | Do not run apt inside a container before the task. |
| `CAIRNML_AGENT_FORCE_SYSTEM_SITE_PACKAGES` | `false` | Give task environments access to the system's site packages. |
| `CAIRNML_AGENT_PACKAGE_PYTORCH_RESOLVE` | `pip` | How PyTorch wheels are resolved: `pip`, `direct` or `none`. |
| `CAIRNML_AGENT_LOCAL_SDK_WHEEL` | unset | Path to a local CairnML SDK wheel to install for the task, built from a source tree or repacked from the installed SDK. Set for the task when a local wheel is used. |
| `CAIRNML_EXTRA_PIP_INSTALL_FLAGS` | unset | Extra flags for every `pip install`, separated by spaces. |
| `CAIRNML_AGENT_EXTRA_PYTHON_PATH` | unset | Extra directories added to the task's Python path. |
| `CAIRNML_AGENT_EXEC_USER` | unset | User the task runs as. |
| `CAIRNML_AGENT_VENV_CACHE_PATH` | unset | Directory of the cached virtual environments. |
| `CAIRNML_CUSTOM_BUILD_OUTPUT` | unset | Output file of the custom build script, as the script reads it. Set for the custom build script. |
| `CAIRNML_CUSTOM_BUILD_TASK_CONFIG_JSON` | unset | Task configuration as JSON, for the custom build script. Set for the custom build script. |
| `CAIRNML_TASK_SCRIPT_ENTRY` | unset | Entry script of the task. Set for the custom build script. |
| `CAIRNML_TASK_WORKING_DIR` | unset | Working directory of the task. Set for the custom build script. |
| `CAIRNML_VENV_PATH` | unset | Virtual environment path of the agent. Set for the custom build script. |
| `CAIRNML_GIT_ROOT` | unset | Path of the cloned Git repository. Set for the custom build script. |
| `CAIRNML_QUEUE_NAME` | unset | Name of the queue the task came from. Used in the `docker_cmd` template. |
| `CAIRNML_AGENT_SERVICES_DOCKER_RESTART` | unset | Restart policy of the Docker containers of services, for example `unless-stopped`. |
| `CAIRNML_AGENT_EXTRA_DOCKER_ARGS` | unset | Extra arguments for every task's `docker run`, separated by spaces. |
| `CAIRNML_AGENT_EXTRA_DOCKER_LABELS` | unset | Labels added to every task's container, separated by spaces. |
| `CAIRNML_AGENT_DOCKER_ARGS_FILTERS` | unset | Whitelist of the Docker arguments a task may use. |
| `CAIRNML_AGENT_DOCKER_ARGS_HIDE_ENV` | unset | Extra variable names whose values are hidden in the Docker command, separated by spaces. |
| `CAIRNML_AGENT_DOCKER_HOST_MOUNT` | unset | Host directories mounted into task containers. |
| `CAIRNML_AGENT_K8S_HOST_MOUNT` | unset | Host directories mounted into task containers on Kubernetes. |
| `CAIRNML_DOCKER_IMAGE` | unset | Docker image of the task. Set for the task. |
| `CAIRNML_DOCKER_SKIP_GPUS_FLAG` | unset | Do not add `--gpus` to `docker run`. |
| `CAIRNML_AGENT_GPU_FRACTIONS` | unset | GPU fractions to offer to tasks, for example `0:0.5`. |

## Git and packages

| Variable | Default | Meaning |
|---|---|---|
| `CAIRNML_GIT_USERNAME` | unset | User name for cloning Git repositories over HTTPS. |
| `CAIRNML_GIT_PASSWORD` | unset | Password or token for cloning Git repositories over HTTPS. |
| `CAIRNML_AGENT_GIT_USER` | unset | User name for Git, set in the configuration for every clone. |
| `CAIRNML_AGENT_GIT_PASS` | unset | Password for Git, set in the configuration for every clone. |
| `CAIRNML_AGENT_GIT_HOST` | unset | Git host the credentials apply to. |
| `CAIRNML_AGENT_GIT_CLONE_VERBOSE` | `false` | Show the output of `git clone`. |
| `CAIRNML_AGENT_GIT_USE_MS_ENTRA_TOKEN` | `false` | Use a Microsoft Entra token for Git over HTTPS. |
| `CAIRNML_AGENT_GIT_USE_AZURE_PAT` | `false` | Use an Azure DevOps personal access token for Git over HTTPS. |
| `CAIRNML_AGENT_SSH_URL_REPLACEMENT_SCHEME` | unset | Scheme that replaces `git@` SSH repository URLs, for example `https`. |

## Kubernetes glue

| Variable | Default | Meaning |
|---|---|---|
| `CAIRNML_K8S_GLUE_KIND` | `pod` | Kind of Kubernetes object the glue starts for a task: `pod` or another kind it supports. |
| `CAIRNML_K8S_GLUE_START_AGENT_SCRIPT_PATH` | `~/__start_agent__.sh` | Script the glue writes to start the agent in a pod. |
| `CAIRNML_AGENT_NO_UPDATE` | unset | Do not update `cairnml-agent` when a container starts. Set in the glue's pods. |

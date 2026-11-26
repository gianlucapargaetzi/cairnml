# Environment variables

Every environment variable the CairnML SDK reads. Each setting has one name, the `CAIRNML_` name. The ClearML-era `TRAINS_` names are no longer read: a `TRAINS_` variable has no effect.

The config file (`cairnml.conf`) can set most of these as well; the variable overrides the file. `cairnml init` writes `~/.cairnml/cairnml.conf`, next to the device key; the earlier `~/cairnml.conf` is still read, and `~/trains.conf` (from the ClearML-based original) is read with a deprecation warning once, so rename it to `~/.cairnml/cairnml.conf`. The file holds only the enrolment and what you set by hand: the defaults come from the installed version, so an upgrade changes them, and `cairnml doctor` reports a file an older version wrote with a copy of them.

Columns: **Name** is the variable; **Read in** is the module that reads it; **Default** is the value when unset (blank: unset means off or not set); **Meaning** is taken from the code's own comments where there are any, and says so where the code does not explain the variable.

| Name | Read in | Default | Meaning |
|------|---------|---------|---------|
| `CAIRNML_ENV` | `backend_config/config.py` | `Environment.default` | Which environment section of the configuration is used. |
| `CAIRNML_CONFIG_FILE` | `backend_config/defs.py` | | Path of a configuration file to read in place of the default one. |
| `CAIRNML_CONFIG_PATH` | `backend_config/defs.py` | | Folder of configuration files to read. |
| `CAIRNML_CONFIG_VERBOSE` | `backend_config/defs.py` | | Log which configuration files are read. |
| `CAIRNML_AUTH_TOKEN` | `backend_api/session/defs.py` | | A sign-in token used instead of the device key. |
| `CAIRNML_API_HOST` | `backend_api/session/defs.py` | | Address of the CairnML API server. |
| `CAIRNML_WEB_HOST` | `backend_api/session/defs.py` | | Address of the CairnML web app. |
| `CAIRNML_API_HOST_VERIFY_CERT` | `backend_api/session/defs.py` | `True` | Verify the server's TLS certificate. |
| `CAIRNML_API_VERBOSE` | `backend_api/session/defs.py` | `False` | Log every API request and response. |
| `CAIRNML_API_DEFAULT_REQ_METHOD` | `backend_api/session/defs.py` | | HTTP method used for API requests. |
| `CAIRNML_API_EXTRA_RETRY_CODES` | `backend_api/session/defs.py` | | Extra HTTP status codes that are retried. |
| `CAIRNML_OFFLINE_MODE` | `backend_api/session/defs.py` | | Run without a server: nothing is sent. |
| `CAIRNML_NO_DEFAULT_SERVER` | `backend_api/session/defs.py` | `True` | Do not fall back to a default server address. |
| `CAIRNML_DEFERRED_TASK_INIT` | `backend_api/session/defs.py` | | Delay the task's initialisation until its first use. |
| `CAIRNML_IGNORE_MISSING_CONFIG` | `backend_api/session/defs.py` | | Do not fail when a configuration file is missing. |
| `CAIRNML_ENABLE_ENV_CONFIG_SECTION` | `backend_api/session/defs.py` | | Read settings from the environment as a configuration section. |
| `CAIRNML_ENABLE_FILES_CONFIG_SECTION` | `backend_api/session/defs.py` | | Read settings from files as a configuration section. |
| `CAIRNML_JOB_TOKEN` | `datasets/credentials.py` | | The token a job uses to reach the server. |
| `CAIRNML_JOB_TOKEN_FILE` | `datasets/credentials.py` | | File holding the job token. |
| `CAIRNML_TASK_ID` | `datasets/dataset.py` | | The task the process belongs to. Set for every job. |
| `CAIRNML_PROC_MASTER_ID` | `automation/controller.py` | | Id of the process that started the pipeline. |
| `CAIRNML_NODE_ID` | `config/defs.py` | | Node number in a multi-node run. |
| `CAIRNML_FORCE_MASTER_NODE` | `config/__init__.py` | | Force this process to be (`1`) or not be (`0`) the master node. |
| `CAIRNML_MULTI_NODE_MASTER` | `task.py` | | Address of the master node in a multi-node run. |
| `CAIRNML_MULTI_NODE_MASTER_DEF_ADDR` | `task.py` | | Default address of the master node. |
| `CAIRNML_MULTI_NODE_MASTER_DEF_PORT` | `task.py` | | Default port of the master node. |
| `CAIRNML_MULTI_NODE_SINGLE_TASK` | `config/defs.py` | `None` | Multi-node reporting: `0` or unset, one task per node; `1` or `2`, multi-node reporting with coloured console output; `-1`, report only from rank 0. |
| `CAIRNML_AGENT_HOST_IP` | `utilities/networking.py` | | The IP address the process reports for its host. |
| `CAIRNML_WORKER_NAME` | `config/defs.py` | | Name the worker reports itself with. |
| `CAIRNML_DOCKER_IMAGE` | `config/defs.py` | | Docker image a task runs in. |
| `CAIRNML_DOCKER_BASH_SCRIPT` | `config/defs.py` | | Shell script run before a task in Docker. |
| `CAIRNML_DEVELOPMENT_DEFAULT_SHELL_BINARY_PATH` | `config/defs.py` | | Shell used for development runs. |
| `CAIRNML_CACHE_DIR` | `config/defs.py` | | Folder of the local cache. |
| `CAIRNML_SECONDARY_CACHE_DIR` | `storage/helper.py` | | Second cache folder, read-only. |
| `CAIRNML_DEFAULT_OUTPUT_URI` | `config/defs.py` | | Where task outputs are stored when a task gives no output location. |
| `CAIRNML_AZURE_STORAGE_USE_DEFAULT_CREDENTIAL` | `backend_config/bucket_config.py` | | Use the Azure default credential chain for storage. |
| `CAIRNML_LOG_LEVEL` | `debugging/log.py` | | Log level of the SDK's own logging. The code notes that it cannot use the constant in `config/defs.py` because of a circular import. |
| `CAIRNML_LOG_STDERR_REDIRECT_LEVEL` | `config/defs.py` | | Log level of the lines written to stderr that are redirected to the log. |
| `CAIRNML_LOG_TASK_TO_BACKEND` | `config/defs.py` | | Send the task's log to the server. |
| `CAIRNML_LOG_ENVIRONMENT` | `config/defs.py` | | Log the environment of the process. |
| `CAIRNML_SUPPRESS_UPDATE_MESSAGE` | `config/defs.py` | | Do not print a message about a newer release. |
| `CAIRNML_BLOCK_PICKLED_ARTIFACTS` | `config/defs.py` | | Refuse to store artifacts as pickles. |
| `CAIRNML_MAX_SERIES_PER_METRIC` | `config/defs.py` | `100` | Most series reported for one metric. |
| `CAIRNML_SET_ITERATION_OFFSET` | `config/defs.py` | | Offset added to the iteration numbers reported. |
| `CAIRNML_SIMULATE_REMOTE_TASK` | `config/defs.py` | | Run the task as if it were on a remote machine. |
| `CAIRNML_TASK_NO_REUSE` | `config/defs.py` | | Do not reuse an existing task. |
| `CAIRNML_RESMON_DEBUG` | `utilities/resource_monitor.py` | | Log the resource monitor's own measurements. |
| `CAIRNML_JUPYTER_PASSWORD` | `config/defs.py` | | Password of the Jupyter server the notebook's repository is detected from (see `backend_interface/task/repo/scriptinfo.py`). |
| `CAIRNML_VCS_REPO_TYPE` | `config/defs.py` | `git` | Version control system of the code. |
| `CAIRNML_VCS_REPO_URL` | `config/defs.py` | | Remote URL of the code repository. |
| `CAIRNML_VCS_COMMIT_ID` | `config/defs.py` | | Commit the code was run from. |
| `CAIRNML_VCS_BRANCH` | `config/defs.py` | | Branch the code was run from. |
| `CAIRNML_VCS_ROOT` | `config/defs.py` | | Root folder of the repository. |
| `CAIRNML_VCS_WORK_DIR` | `config/defs.py` | | Working folder inside the repository. |
| `CAIRNML_VCS_ENTRY_POINT` | `config/defs.py` | | Entry script of the run, relative to the repository. |
| `CAIRNML_VCS_STATUS` | `config/defs.py` | | Uncommitted-changes status of the repository. |
| `CAIRNML_VCS_DIFF` | `config/defs.py` | | Uncommitted diff of the repository, recorded with the run. |

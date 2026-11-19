from __future__ import print_function, division, unicode_literals

import base64
import errno
import functools
import json
import logging
import os
import os.path
import random
import re
import shlex
import stat
import shutil
import signal
import string
import socket
import subprocess
import sys
import traceback
from collections import defaultdict
from copy import deepcopy, copy
from datetime import datetime
from functools import partial
from os.path import basename
from tempfile import mkdtemp, NamedTemporaryFile
from threading import Thread
from time import sleep, time
from typing import Text, Optional, Any, Tuple, List, Dict  # noqa: F401  # used in type comments

import attr
from pathlib import Path
from urllib.parse import quote  # noqa

from cairnml.backend_api.services import queues as queues_api
from cairnml.backend_api.services import tasks as tasks_api
from cairnml.backend_api.services import workers as workers_api
from cairnml.backend_config.utils import apply_environment, apply_files
from cairnml.idp.device import secure_folder
from cairnml_agent.backend_api.session.defs import (
    ENV_ENABLE_ENV_CONFIG_SECTION,
    ENV_ENABLE_FILES_CONFIG_SECTION,
    ENV_VENV_CONFIGURED,
    ENV_PROPAGATE_EXITCODE,
    ENV_MULTI_NODE_SINGLE_TASK,
)
from cairnml_agent.backend_config.config import Config
from cairnml_agent.helper.environment.converters import text_to_int
from cairnml_agent.commands.base import resolve_names, ServiceCommandSection
from cairnml_agent.commands.resolver import resolve_default_container
from cairnml_agent.commands.docker_run import DockerRun
from cairnml_agent.definitions import (
    ENVIRONMENT_SDK_PARAMS,
    PROGRAM_NAME,
    ENV_DOCKER_IMAGE,
    ENV_TASK_EXECUTE_AS_USER,
    ENV_DOCKER_HOST_MOUNT,
    ENV_TASK_EXTRA_PYTHON_PATH,
    ENV_AGENT_GIT_USER,
    ENV_AGENT_GIT_PASS,
    ENV_AGENT_GIT_HOST,
    ENV_WORKER_ID,
    ENV_WORKER_TAGS,
    ENV_DOCKER_SKIP_GPUS_FLAG,
    ENV_AGENT_AUTH_TOKEN,
    ENV_AGENT_DISABLE_SSH_MOUNT,
    ENV_SSH_AUTH_SOCK,
    ENV_AGENT_SKIP_PIP_VENV_INSTALL,
    ENV_EXTRA_DOCKER_ARGS,
    ENV_CUSTOM_BUILD_SCRIPT,
    ENV_AGENT_SKIP_PYTHON_ENV_INSTALL,
    WORKING_STANDALONE_DIR,
    ENV_DEBUG_INFO,
    ENV_CHILD_AGENTS_COUNT_CMD,
    ENV_DOCKER_ARGS_FILTERS,
    ENV_FORCE_SYSTEM_SITE_PACKAGES,
    ENV_SERVICES_DOCKER_RESTART,
    ENV_CONFIG_BC_IN_STANDALONE,
    ENV_EXTRA_DOCKER_LABELS,
    ENV_AGENT_FORCE_CODE_DIR,
    ENV_AGENT_FORCE_EXEC_SCRIPT,
    ENV_TEMP_STDOUT_FILE_DIR,
    ENV_AGENT_FORCE_TASK_INIT,
    ENV_AGENT_DEBUG_GET_NEXT_TASK,
    ENV_ABORT_CALLBACK_CMD,
    ENV_ABORT_CALLBACK_CMD_TIMEOUT,
    ENV_QUEUE_POLL_FREQ_SEC,
    ENV_STATUS_REPORT_FREQ_SEC,
)
from cairnml_agent.definitions import WORKING_REPOSITORY_DIR, PIP_EXTRA_INDICES
from cairnml_agent.errors import (
    APIError,
    CommandFailedError,
    Sigterm,
    SkippedCustomBuildScript,
    CustomBuildScriptFailed,
)
from cairnml_agent.helper.base import (
    return_list,
    print_parameters,
    warning,
    normalize_path,
    check_directory_path,
    select_for_platform,
    mkstemp as safe_mkstemp,
    print_table,
    safe_remove_file,
    is_windows_platform,
    rm_tree,
    is_conda,
    named_temporary_file,
    ExecutionInfo,
    HOCONEncoder,
    error,
    get_python_path,
    is_linux_platform,
    rm_file,
    add_python_path,
    safe_remove_tree,
    get_python_version,
    dump_flat_dict,
    check_is_binary_python_or_bash,
)
from cairnml_agent.helper.console import print_text, decode_binary_lines
from cairnml_agent.helper.environment.converters import strtobool
from cairnml.utilities.gpu_query import detect_rocm_env
from cairnml_agent.helper.os.daemonize import daemonize_process
from cairnml_agent.helper.package.base import PackageManager, get_specific_package_version
from cairnml_agent.helper.package.conda_api import CondaAPI
from cairnml_agent.helper.package.external_req import ExternalRequirements, OnlyExternalRequirements
from cairnml_agent.helper.package.pip_api.system import SystemPip
from cairnml_agent.helper.package.pip_api.venv import VirtualenvPip
from cairnml_agent.helper.package.poetry_api import PoetryConfig, PoetryAPI
from cairnml_agent.helper.package.uv_api import UvConfig, UvAPI
from cairnml_agent.helper.package.post_req import PostRequirement
from cairnml_agent.helper.package.priority_req import (
    PriorityPackageRequirement,
    PackageCollectorRequirement,
    CachedPackageRequirement,
    LocalSdkRequirement,
    LOCAL_SDK_WHEEL_ENV,
)
from cairnml_agent.helper.package.pytorch import PytorchRequirement
from cairnml_agent.helper.package.requirements import (
    RequirementsManager,
)
from cairnml_agent.helper.process import (
    kill_all_child_processes,
    WorkerParams,
    ExitStatus,
    Argv,
    COMMAND_SUCCESS,
    get_bash_output,
    shutdown_docker_process,
    get_docker_id,
    commit_docker,
    terminate_process,
    check_if_command_exists,
    terminate_all_child_processes,
    find_executable,
    Executable,  # noqa: F401  # used in type comments
)
from cairnml_agent.helper.job_token import (
    CONTAINER_DIR,
    TOKEN_FILE_NAME,
    TokenRenewer,
    request_job_token,
    write_job_token,
)
from cairnml_agent.helper.repo import (
    clone_repository_cached,
    fix_package_import_diff_patch,
    patch_add_task_init_call,
    RepoInfo,  # noqa: F401  # used in type comments
    VCS,  # noqa: F401  # used in type comments
)
from cairnml_agent.helper.resource_monitor import ResourceMonitor
from cairnml_agent.helper.singleton import Singleton
from cairnml_agent.helper.docker_args import DockerArgsSanitizer, DockerArgsTemplateResolver
from cairnml_agent.idp.device import DEVICE_KEY_PATH, exposes_device_key
from cairnml_agent.session import Session
from .events import Events
from cairnml_agent.commands import bootstrap

DOCKER_ROOT_CONF_FILE = "/tmp/cairnml.conf"  # assuming we can always access/mount this file
DOCKER_DEFAULT_CONF_FILE = "~/default_cairnml.conf"
# a queue with this tag is not pulled from by the worker (the queue is switched off for workers)
QUEUE_TAG_OFF = "force_workers:off"


sys_random = random.SystemRandom()


VENV_MODE_WARNING = (
    "WARNING: --allow-venv runs jobs on this host as the agent's own user. A job can read the agent's device key "
    "(~/.cairnml/agent/device.key) and sign in as the user who enrolled this agent, with a token that lasts until "
    "the device is revoked. Use it only for code you fully trust, or run the agent with --docker."
)


def check_job_mounts(host_paths) -> None:
    """Refuse a folder mounted into a job that holds the agent's device key (the key, its folder or a folder above)."""
    for path in host_paths:
        if path and exposes_device_key(path):
            raise ValueError(
                "The job folder {} would give the job the agent's device key ({}). Choose a folder outside {}.".format(
                    path, DEVICE_KEY_PATH, DEVICE_KEY_PATH.parent
                )
            )


def job_environment(base_env, job_token=None, job_token_file=None) -> dict:
    """The environment of a job started on this host. The agent's own auth token is never passed on: the job gets
    its own job token."""
    env = dict(base_env)
    for name in ENV_AGENT_AUTH_TOKEN.vars:
        env.pop(name, None)
    env["CAIRNML_JOB_TOKEN"] = job_token
    env["CAIRNML_JOB_TOKEN_FILE"] = str(job_token_file)
    return env


def check_execution_mode(use_docker: bool, allow_venv: bool) -> Optional[str]:
    """
    Jobs run in a Docker container. A Python virtual environment on the host is allowed only when the
    agent is started with --allow-venv, because such a job can read the agent's own device key.
    Returns the warning to show when jobs run on the host, otherwise None.
    """
    if not use_docker and not allow_venv:
        raise ValueError(
            "The agent runs jobs in Docker containers only. Start it with --docker. "
            "To run jobs in a virtual environment on this host instead, pass --allow-venv "
            "(not recommended: such a job can read the agent's device key)."
        )
    return None if use_docker else VENV_MODE_WARNING


def run_mode_for(use_docker: bool, allow_venv: bool) -> str:
    """The mode the agent runs jobs in, as reported to the server: a Docker container, a virtual environment
    on the host (--allow-venv), or the host itself."""
    if use_docker:
        return "docker"
    return "venv" if allow_venv else "host"


@attr.s
class LiteralScriptManager(object):
    """
    Manage notebook tasks
    """

    venv_folder = attr.ib(type=str)

    @staticmethod
    def is_literal_script(task):
        # type: (tasks_api.Task) -> bool
        """
        Returns whether a task object represents a notebook task
        """
        script = task.script
        if not script:
            return False
        diff = script.diff
        if not diff:
            return False

        # test git diff prefix
        if diff.lstrip().lower().startswith("diff "):
            return False

        # test git submodule prefix
        # noinspection PyBroadException
        try:
            if diff.lstrip().lower().startswith("submodule ") and diff.splitlines()[1].lstrip().lower().startswith(
                "diff "
            ):
                return False
        except Exception:
            pass

        # none of the above
        return True

    @staticmethod
    def write(task, directory, entry_point=None):
        # type: (tasks_api.Task, str, Optional[str]) -> str
        """
        Create notebook file for ``task`` in ``directory``
        """
        if entry_point:
            full_path = normalize_path(Text(directory), entry_point)
            if os.path.exists(full_path):
                return entry_point

            with open(full_path, "wt", encoding="utf-8") as f:
                f.write(task.script.diff)
                return full_path

        with named_temporary_file(
            delete=False, prefix="script_", suffix=".py", dir=Text(directory), mode="wt", encoding="utf-8"
        ) as f:
            f.write(task.script.diff)
            return f.name

    def create_notebook_file(self, task, execution, repo_info):
        # type: (tasks_api.Task, ExecutionInfo, Optional[RepoInfo]) -> Tuple[str, str, str]
        """
        Create notebook file in appropriate location
        :return: directory and script path
        """
        log = logging.getLogger(__name__)
        target_file_name_module_call = None
        is_python_binary, is_bash_binary = check_is_binary_python_or_bash(task.script.binary)

        if execution.entry_point and (
            execution.entry_point.strip().startswith("-m ") or execution.entry_point.strip().startswith("-c ")
        ):
            # this is a module we cannot use it as file name
            target_file_name_module_call = (
                "untitled.sh" if is_bash_binary and execution.entry_point.strip().startswith("-c ") else "untitled.py"
            )
            # let's parse the working_dir and override the default literal file
            if execution.working_dir and ":" in execution.working_dir:
                execution.working_dir, target_file_name_module_call = execution.working_dir.split(":", 1)
            log.warning(
                "found task with `script.entry_point` using a module defaulting to: {}".format(
                    target_file_name_module_call
                )
            )

        if repo_info and repo_info.root:
            location = Path(repo_info.root, execution.working_dir)
        else:
            if execution.working_dir and execution.working_dir.strip() != ".":
                log.warning(
                    "found task with `script.working_dir` (`%s`) but without `script.repository`, ignoring",
                    execution.working_dir,
                )
            location = None

        location = location or (repo_info and repo_info.root)
        if not location:
            location = Path(self.venv_folder, WORKING_STANDALONE_DIR)
            location.mkdir(exist_ok=True, parents=True)

        if not execution.entry_point:
            execution.entry_point = "untitled.sh" if is_bash_binary else "untitled.py"
        elif not target_file_name_module_call:
            # try shlex parsing first, decide if we managed based on the filename extension
            try:
                file_name = shlex.split(execution.entry_point)[0].split(os.path.sep)[-1]
                if is_bash_binary and (file_name.endswith(".sh") or file_name.endswith(".bash")):
                    target_file_name_module_call = file_name
                elif is_python_binary and file_name.endswith(".py"):
                    target_file_name_module_call = file_name
            except Exception:
                pass

        log.debug("selected execution directory: {}".format(location))
        target_file = self.write(task, location, target_file_name_module_call or execution.entry_point)
        return (Text(location), execution.entry_point if target_file_name_module_call else target_file, target_file)


_LOCAL_WHEELS = {}


def is_source_tree(folder: Path, name: str) -> bool:
    """True when ``folder`` is a source tree of the package ``name``: its pyproject.toml gives that name, or it still
    has a setup.py. A pyproject.toml of some other project (a virtual environment can sit inside one) does not count."""
    if (folder / "setup.py").is_file():
        return True
    try:
        text = (folder / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(r'^name\s*=\s*"{}"\s*$'.format(re.escape(name)), text, re.M) is not None


def build_local_wheel(source: Path, name: str) -> str:
    """The path of a wheel of the package ``name``, built from a source tree of this machine, once per process.

    Job containers install from these wheels and never from PyPI (KNOWN DEFECT D1, cairnml-agent issue #1).
    """
    key = str(source)
    if key not in _LOCAL_WHEELS:
        if not is_source_tree(source, name):
            raise CommandFailedError(
                "{} is not a source tree of {} (no pyproject.toml naming it, and no setup.py): "
                "the job containers need the local wheel built from it".format(source, name)
            )
        out = Path(mkdtemp(prefix="cairnml-wheel-"))
        # pip writes egg-info and build output into the source tree, which may be read-only for this user (the
        # agent can run from a tree owned by another user). Build from a copy so the tree is never written to.
        build_src = Path(mkdtemp(prefix="cairnml-src-")) / source.name
        shutil.copytree(
            source, build_src, ignore=shutil.ignore_patterns(".git", "build", "dist", "*.egg-info", "__pycache__")
        )
        for root, dirs, files in os.walk(
            build_src
        ):  # the copy keeps read-only modes, so make it writable for the build
            for name in dirs + files + ["."]:
                path = os.path.join(root, name)
                os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
        subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(out), str(build_src)],
            check=True,
            timeout=900,
        )
        wheels = sorted(out.glob("*.whl"))
        if not wheels:
            raise CommandFailedError("building the wheel of {} produced no file".format(source))
        _LOCAL_WHEELS[key] = str(wheels[-1])
    return _LOCAL_WHEELS[key]


def local_agent_wheel() -> str:
    """The wheel of this cairnml-agent, built from the source tree it runs from."""
    return build_local_wheel(Path(__file__).resolve().parents[2], "cairnml-agent")


def local_sdk_wheel() -> str:
    """The wheel of the CairnML SDK this machine has, built from its source tree.

    The SDK is found through its package path (it can be a namespace package, with no __file__); its source tree is
    the first folder above the package that is a source tree of the SDK (see ``is_source_tree``).
    """
    import cairnml

    for package_dir in cairnml.__path__:
        for folder in [Path(package_dir).resolve(), *Path(package_dir).resolve().parents]:
            if is_source_tree(folder, "cairnml"):
                return build_local_wheel(folder, "cairnml")
    raise CommandFailedError("no source tree of cairnml above the CairnML SDK package: cannot build its wheel")


def get_repo_auth_string(user, password):
    # type: (Text, Text) -> Text
    """
    Return user:password only if user and password are valid
    :param user: username
    :param password:
    :return: URL authentication string
    """
    if not (user and password):
        return ""
    return ":".join(map(quote, (user, password)))


CONCAT_CMD = select_for_platform(linux=" && ", windows=" & ")


class TaskNotFoundError(APIError):
    pass


class TaskStopReason(object):
    no_stop = 0  # type: TaskStopReason
    stopped = 1  # type: TaskStopReason
    reset = 2  # type: TaskStopReason
    status_changed = 3  # type: TaskStopReason
    exception = 4  # type: TaskStopReason
    not_found = 5  # type: TaskStopReason

    @classmethod
    def to_str(cls, reason):
        for k, v in vars(cls).items():
            if isinstance(v, int) and v == reason:
                return k
        return "unknown"


def get_task(session, task_id, **kwargs):
    """Read the task with its hidden ones searched (search_hidden, from API v2.14)"""
    result = session.send(session.current(tasks_api.GetAllRequest)(id=[task_id], search_hidden=True, **kwargs))
    if not result.ok():
        raise APIError(result)
    if not result.response:
        raise APIError(result, extra_info="Invalid response")
    if not result.response.tasks:
        raise TaskNotFoundError(result)
    return result.response.tasks[0]


def get_next_task(session, queue):
    """
    Returns dict of the next task in the queue
    """
    debug = ENV_AGENT_DEBUG_GET_NEXT_TASK.get()
    request = session.current(queues_api.GetNextTaskRequest)(queue=queue)
    if debug:
        print(f"debug> get_next_task: {request.method} payload {request.to_dict()}")
    result = session.send(request)
    if debug:
        print(f"debug> get_next_task: response {result.meta.result_code} data {result.response_data}")
    if not result.ok():
        raise APIError(result)
    if result.response_data is None:
        raise APIError(result, extra_info="Invalid response")
    return result.response_data


def get_task_fields(session, task_id, fields: list, log=None) -> dict:
    """
    Returns dict with Task docker container setup {container: '', arguments: '', setup_shell_script: ''}
    """
    # noinspection PyBroadException
    try:
        results = {}
        result = session.send_api(
            session.current(tasks_api.GetAllRequest)(id=[task_id], only_fields=list(fields), search_hidden=True)
        ).to_dict()["tasks"][0]
        for field in fields:
            cur = result
            for part in field.split("."):
                if part.isdigit():
                    cur = cur[part]
                else:
                    cur = cur.get(part, {})
            results[field] = cur
        return results
    except Exception as ex:
        if log:
            log.error("Failed obtaining values for task fields %s: %s", fields, ex)
        pass
    return {}


def get_task_container(session, task_id, ignore_match_rules=False, allow_force_container_rules=True):
    """
    Returns dict with Task docker container setup {container: '', arguments: '', setup_shell_script: ''}
    """
    raw_container = {}
    if session.declares("tasks", "get_all", "search_hidden"):
        try:
            container = (
                session.send_api(
                    session.current(tasks_api.GetAllRequest)(
                        id=[task_id], only_fields=["container"], search_hidden=True
                    )
                )
                .to_dict()["tasks"][0]
                .get("container")
            )
            raw_container = copy(container or {})
            if container.get("arguments"):
                container["arguments"] = shlex.split(str(container.get("arguments")).strip())
            if container.get("image"):
                container["image"] = container.get("image").strip()
        except (ValueError, TypeError):
            container = {}
    else:
        response = get_task(session, task_id, only_fields=["execution.docker_cmd"])
        container = {}
        if response.execution:
            task_docker_cmd_parts = shlex.split(str(response.execution.docker_cmd or "").strip())
            if task_docker_cmd_parts:
                try:
                    container = dict(
                        image=task_docker_cmd_parts[0],
                        arguments=task_docker_cmd_parts[1:] if len(task_docker_cmd_parts[0]) > 1 else "",
                    )
                except (ValueError, TypeError):
                    pass
            raw_container = copy(container or {})
            if raw_container.get("arguments"):
                raw_container["arguments"] = " ".join(shlex.quote(x) for x in raw_container["arguments"])

    no_default_container = not container or not container.get("image")
    if no_default_container or allow_force_container_rules and session.declares("tasks", "get_all", "search_hidden"):
        original_container = copy(container or {})
        updated_container, entry = resolve_default_container(
            session=session,
            task_id=task_id,
            container_config=original_container,
            ignore_match_rules=ignore_match_rules and not no_default_container,
        )
        if entry and no_default_container and not ignore_match_rules:
            # if we do not have a default container image / args, use the defaults from the resolver
            container = updated_container
            updated = True
        elif entry and allow_force_container_rules and updated_container.get("force_container_rules"):
            # if we allow to force rules (and we have requested container)
            # and the 'force_container_rules' is turned on in the rule, then overwrite container
            container = updated_container
            updated = True
        else:
            updated = False

        if updated:
            print("INFO: Updating Task Container with matched rule:\n{}".format(json.dumps(entry or {})))

            # make sure we pop the new added fields
            container.pop("force_container_rules", None)

            # check if we need to update the Task based on the new matched container defaults or overrides
            update_back_task = container.pop("update_back_task", None) or updated_container.pop(
                "update_back_task", None
            )
            if update_back_task and container != original_container and session.declares("tasks", "edit", "container"):
                # update back the task
                print("INFO: Updating the Task with the selected container with rule")
                try:
                    res = session.send(
                        session.current(tasks_api.EditRequest)(
                            task=task_id,
                            force=True,
                            container={
                                "image": str(container.get("image") or ""),
                                # fix if we are left with the list of arguments, take the original arguments text,
                                # because combining back the list is a bit off
                                "arguments": str(raw_container.get("arguments") or "")
                                if isinstance(container.get("arguments"), (list, tuple))
                                else str(container.get("arguments") or ""),
                                "setup_shell_script": str(container.get("setup_shell_script") or ""),
                            },
                        )
                    )
                    if not res.ok():
                        raise Exception("failed setting container property")
                except Exception as ex:
                    print("WARNING: failed setting container properties for task '{}': {}".format(task_id, ex))

            # make sure we preserve backwards compatibility with the expected entries types
            if isinstance(container.get("arguments"), str):
                container["arguments"] = shlex.split(str(container.get("arguments") or "").strip())
            if container.get("image"):
                container["image"] = container.get("image").strip()

    return container


def set_task_container(session, task_id, docker_image=None, docker_arguments=None, docker_bash_script=None):
    if docker_arguments and isinstance(docker_arguments, str):
        docker_arguments = [docker_arguments]

    if session.declares("tasks", "edit", "container"):
        container = dict(
            image=docker_image or "",
            arguments=" ".join(docker_arguments) if docker_arguments else "",
            setup_shell_script=docker_bash_script or "",
        )
        result = session.send(session.current(tasks_api.EditRequest)(task=task_id, container=container, force=True))
        return result.ok()
    else:
        return session.send_api(
            session.current(tasks_api.EditRequest)(
                task_id,
                force=True,
                execution=dict(  # noqa
                    docker_cmd=" ".join([docker_image] + docker_arguments)
                    if docker_arguments
                    else str(docker_image or "")
                ),
            )
        )


class TaskStopSignal(object):
    """
    Follow task status and signals when it should be stopped
    """

    _number_of_consecutive_reset_tests = 4
    statuses = tasks_api.TaskStatusEnum
    unexpected_statuses = [
        statuses.closed,
        statuses.stopped,
        statuses.failed,
        statuses.published,
        statuses.queued,
    ]
    default = TaskStopReason.no_stop
    stopping_message = "stopping"
    property_abort_callback_completed = "_abort_callback_completed"
    property_abort_callback_timeout = "_abort_callback_timeout"
    property_abort_poll_freq = "_abort_poll_freq"

    def __init__(self, command, session, events_service, task_id, bash_cwd=None):
        # type: (Worker, Session, Events, Text, Text) -> None
        """
        :param command: workers command
        :param session: command session
        :param events_service: events service object
        :param task_id: followed task ID
        :param bash_cwd: cwd for bash on_abort callback
        """
        self.command = command
        self.session = session
        self.events_service = events_service
        self.worker_id = command.worker_id
        self._task_reset_state_counter = 0
        self.task_id = task_id
        self._support_callback = None
        self._active_callback_timestamp = None
        self._active_callback_timeout = None
        self._abort_callback_max_timeout = float(self.session.config.get("agent.abort_callback_max_timeout", 1800))
        self._bash_callback_cwd = bash_cwd
        self._bash_callback = None
        self._bash_callback_timeout = None
        self._bash_callback_thread = None
        self._self_monitor_thread = None

    @classmethod
    def check_registered_bash_callback(cls):
        return bool(
            (
                ENV_ABORT_CALLBACK_CMD.get() or os.environ.get(ENV_ABORT_CALLBACK_CMD.vars[0] + "_REGISTERED") or ""
            ).strip()
        )

    def register_bash_callback(self):
        if self._bash_callback:
            return
        # check if the env variable defined a callback
        if not (ENV_ABORT_CALLBACK_CMD.get() or "").strip():
            return
        self._bash_callback = shlex.split(ENV_ABORT_CALLBACK_CMD.get())
        # make sure we are re-testing in subprocesses
        os.environ[ENV_ABORT_CALLBACK_CMD.vars[0] + "_REGISTERED"] = ENV_ABORT_CALLBACK_CMD.get()
        ENV_ABORT_CALLBACK_CMD.pop()

        # noinspection PyBroadException
        try:
            self._bash_callback_timeout = int((ENV_ABORT_CALLBACK_CMD_TIMEOUT.get() or "").strip() or 180)
        except Exception:
            self._bash_callback_timeout = 180

        # update the task runtime properties
        try:
            task_info = self.session.send_api(
                self.session.current(tasks_api.GetAllRequest)(id=[self.task_id], only_fields=["runtime"])
            ).to_dict()["tasks"][0]
            runtime_properties = task_info.get("runtime") or {}
            runtime_properties[self.property_abort_callback_timeout] = str(self._bash_callback_timeout)
            runtime_properties[self.property_abort_poll_freq] = str(10)
            self.session.send_api(
                self.session.current(tasks_api.EditRequest)(task=self.task_id, runtime=runtime_properties, force=True)
            )
        except Exception as ex:
            print("WARNING: failed registering bash callback: {}".format(ex))
            return

        print(
            "INFO: registering bash callback, timout {}s: {}".format(self._bash_callback_timeout, self._bash_callback)
        )

    def start_monitor_thread(self, polling_interval_sec=10):
        self.register_bash_callback()

        if self._self_monitor_thread:
            return
        self._self_monitor_thread = Thread(target=self._monitor_thread_loop, args=(polling_interval_sec,))
        self._self_monitor_thread.daemon = True
        self._self_monitor_thread.start()
        print("INFO: bash on_abort monitor thread started")

    def stop_monitor_thread(self, wait_on_abort_timeout=-1):
        # if no on_abort callback was launched, or it's done, nothing to do
        if not self._bash_callback_thread or not self._self_monitor_thread:
            self._self_monitor_thread = None
            return

        # we need to wait for the on_abort callback to be done
        if wait_on_abort_timeout and wait_on_abort_timeout < 0:
            wait_on_abort_timeout = self._abort_callback_max_timeout

        if wait_on_abort_timeout:
            tic = time()
            while self._bash_callback_thread and (time() - tic < wait_on_abort_timeout):
                sleep(1)

        self._self_monitor_thread = None

    def _monitor_thread_loop(self, polling_interval_sec=10):
        while self._self_monitor_thread is not None:
            sleep(polling_interval_sec)
            stop_reason = self.test()
            if stop_reason != TaskStopSignal.default:
                # mark quit loop
                self._self_monitor_thread = None
                break

    def _bash_callback_launch_thread(self):
        command = Argv(*self._bash_callback)
        print("INFO: running bash on_abort callback: {}".format(command.pretty()))
        try:
            command.check_call(cwd=self._bash_callback_cwd or None)
        except Exception as ex:
            print("WARNING: failed running bash callback: {}".format(ex))

        # update the task runtime properties
        try:
            task_info = self.session.send_api(
                self.session.current(tasks_api.GetAllRequest)(id=[self.task_id], only_fields=["runtime"])
            ).to_dict()["tasks"][0]
            runtime_properties = task_info.get("runtime") or {}
            runtime_properties[self.property_abort_callback_completed] = str(1)
            self.session.send_api(
                self.session.current(tasks_api.EditRequest)(task=self.task_id, runtime=runtime_properties, force=True)
            )
        except Exception as ex:
            print("WARNING: failed updating bash callback completed: {}".format(ex))

        # mark we are done
        self._bash_callback_thread = None
        return

    def test(self):
        # type: () -> TaskStopReason
        """
        Returns whether task should stop and for what reason,
        returns TaskStopReason.no_stop if task shouldn't stop.
        Catches and logs exceptions.
        """
        try:
            return self._test()
        except TaskNotFoundError:
            return TaskStopReason.not_found
        except Exception as ex:
            self.command.log_traceback(ex)
            # make sure we break nothing
            return TaskStopSignal.default

    def _wait_for_abort_callback(self):
        if not self._support_callback:
            return None

        if self._active_callback_timestamp:
            if time() - self._active_callback_timestamp < self._active_callback_timeout:
                # print("waiting for callback to complete")
                self.command.log("waiting for callback to complete")
                # check state
                cb_completed = None
                try:
                    task_info = self.session.send_api(
                        self.session.current(tasks_api.GetAllRequest)(
                            id=[self.task_id],
                            only_fields=[
                                "status",
                                "status_message",
                                "runtime.{}".format(self.property_abort_callback_completed),
                            ],
                        )
                    ).to_dict()
                    cb_completed = task_info["tasks"][0]["runtime"].get(self.property_abort_callback_completed, None)
                except:  # noqa
                    pass

                if not bool(cb_completed):
                    return False

                msg = "Task abort callback completed in {:.2f} seconds".format(time() - self._active_callback_timestamp)
            else:
                msg = "Task abort callback timed out [timeout: {}, elapsed: {:.2f}]".format(
                    self._active_callback_timeout, time() - self._active_callback_timestamp
                )

            self.command.send_logs(self.task_id, ["### " + msg + " ###"], session=self.session)
            return True

        # check if abort callback is turned on
        abort_timeout, poll_timeout, cb_completed = self._get_abort_callback_stat()

        if not abort_timeout:
            # no callback set we can leave
            return None

        try:
            timeout = min(float(abort_timeout) + float(poll_timeout), self._abort_callback_max_timeout)
        except:  # noqa
            self.command.log(
                "Failed parsing runtime timeout shutdown callback [{}, {}]".format(abort_timeout, poll_timeout)
            )
            return None

        self.command.send_logs(
            self.task_id,
            ["### Task abort callback timeout set, waiting for max {} sec ###".format(timeout)],
            session=self.session,
        )

        # launch bash callback if exists
        if self._bash_callback and not self._bash_callback_thread:
            print("INFO: bash on_abort callback thread started")
            self._bash_callback_thread = Thread(target=self._bash_callback_launch_thread)
            self._bash_callback_thread.daemon = True
            self._bash_callback_thread.start()

        self._active_callback_timestamp = time()
        self._active_callback_timeout = timeout
        return bool(cb_completed)

    def _get_abort_callback_stat(self):
        # TODO: add retries on network error with timeout
        try:
            task_info = self.session.send_api(
                self.session.current(tasks_api.GetAllRequest)(
                    id=[self.task_id],
                    only_fields=[
                        "status",
                        "status_message",
                        "runtime.{}".format(self.property_abort_callback_timeout),
                        "runtime.{}".format(self.property_abort_poll_freq),
                        "runtime.{}".format(self.property_abort_callback_completed),
                    ],
                )
            ).to_dict()
            abort_timeout = task_info["tasks"][0]["runtime"].get(self.property_abort_callback_timeout, 0)
            poll_timeout = task_info["tasks"][0]["runtime"].get(self.property_abort_poll_freq, 0)
            cb_completed = task_info["tasks"][0]["runtime"].get(self.property_abort_callback_completed, None)
        except:  # noqa
            abort_timeout = None
            poll_timeout = None
            cb_completed = None

        return abort_timeout, poll_timeout, cb_completed

    def was_abort_function_called(self, process_error_code=None):
        if not self._support_callback:
            return False

        if self._active_callback_timestamp:
            return True

        # if the process error code is SIGKILL (exit code 137) -
        # check the runtime info of the Task - it might have killed itself because it was aborted
        if process_error_code in (-9, 137):
            # check if abort callback is turned on
            _, _, cb_completed = self._get_abort_callback_stat()
            if cb_completed:
                return True

        return False

    def _test(self):
        # type: () -> TaskStopReason
        """
        "Unsafe" version of test()
        """
        if self._support_callback is None:
            # test if backend support callback
            self._support_callback = self.session.declares("tasks", "edit", "runtime")

        task_info = get_task(self.session, self.task_id, only_fields=["status", "status_message"])
        status = task_info.status
        message = task_info.status_message

        if status == self.statuses.in_progress and self.stopping_message in message:
            self.command.log(
                "task status_message has '%s', task will terminate",
                self.stopping_message,
            )
            # actively waiting for task to complete
            if self._wait_for_abort_callback() is False:
                return TaskStopReason.no_stop
            return TaskStopReason.stopped

        if status in self.unexpected_statuses:  # ## and "worker" not in message:
            self.command.log("unexpected status change, task will terminate")
            # actively waiting for task to complete
            if self._wait_for_abort_callback() is False:
                return TaskStopReason.no_stop
            return TaskStopReason.exception if status == self.statuses.failed else TaskStopReason.status_changed

        if status == self.statuses.created:
            if self._task_reset_state_counter >= self._number_of_consecutive_reset_tests:
                self.command.log("task was reset, task will terminate")
                # actively waiting for task to complete
                if self._wait_for_abort_callback() is False:
                    return TaskStopReason.no_stop
                return TaskStopReason.reset

            self._task_reset_state_counter += 1
            warning_msg = "Warning: Task {} was reset! if state is consistent we shall terminate ({}/{}).".format(
                self.task_id,
                self._task_reset_state_counter,
                self._number_of_consecutive_reset_tests,
            )

            if self.events_service:
                self.events_service.send_log_events(
                    self.worker_id,
                    task_id=self.task_id,
                    lines=[warning_msg],
                    level="WARNING",
                )
            print(warning_msg)
        else:
            self._task_reset_state_counter = 0

        return TaskStopReason.no_stop


class Worker(ServiceCommandSection):
    _pip_extra_index_url = PIP_EXTRA_INDICES

    _default_pip = tuple()

    _requirement_substitutions = (
        LocalSdkRequirement,
        PytorchRequirement,
        PriorityPackageRequirement,
        PostRequirement,
        ExternalRequirements,
        partial(PackageCollectorRequirement, collect_package=["trains"]),
        partial(PackageCollectorRequirement, collect_package=["cairnml"]),
        partial(PackageCollectorRequirement, collect_package=["nbconvert"]),
        partial(PackageCollectorRequirement, collect_package=["ipython"]),
    )

    # default poll queues every _polling_interval seconds
    _polling_interval = ENV_QUEUE_POLL_FREQ_SEC.get() or 5.0

    # machine status update intervals, seconds
    _machine_update_interval = ENV_STATUS_REPORT_FREQ_SEC.get() or 30.0

    # message printed before starting task logging,
    # it will be parsed by services_mode, to identify internal docker logging start
    _task_logging_start_message = "Running task '{}'"
    # last message before passing control to the actual task
    _task_logging_pass_control_message = "Running task id [{}]:"

    # label with worker id for worker agent docker in services mode
    _worker_label = "cairnml-worker-id={}"
    # label with parent worker id for worker agent docker in services mode
    _parent_worker_label = "cairnml-parent-worker-id={}"

    _run_as_user_home = "/cairnml_agent_home"
    _docker_fixed_user_cache = "/cairnml_agent_cache"
    _temp_cleanup_list = []

    hostname_task_runtime_prop = "_exec_agent_hostname"

    is_daemon = False

    @property
    def service(self):
        """Worker command service endpoint"""
        return "workers"

    @property
    def _task_status_change_message(self):
        return "Changed by {} {}".format(PROGRAM_NAME, self.worker_id)

    @staticmethod
    def register_signal_handler():
        def handler(*_):
            for f in Worker._temp_cleanup_list + [Singleton.get_pid_file()]:
                safe_remove_tree(f)
            raise Sigterm()

        signal.signal(signal.SIGTERM, handler)

    def __init__(self, *args, **kwargs):
        super(Worker, self).__init__(*args, **kwargs)
        self._debug_context = ENV_DEBUG_INFO.get()
        self.monitor = None
        self._bootstrap = False
        self._compiled = False
        self.log = self._session.get_logger(__name__)
        self.register_signal_handler()
        self._worker_registered = False

        self._apply_extra_configuration()

        self.is_conda = is_conda(self._session.config)  # type: bool
        # Add extra index url - system wide
        extra_url = None
        # noinspection PyBroadException
        try:
            if self._session.config.get("agent.package_manager.extra_index_url", None):
                extra_url = self._session.config.get("agent.package_manager.extra_index_url", [])
                if not isinstance(extra_url, (tuple, list)):
                    extra_url = [extra_url]
                # put external pip url before default ones, so we first look for packages there
                for e in reversed(extra_url):
                    self._pip_extra_index_url.insert(0, e)
        except Exception:
            self.log.warning("Failed adding extra-index-url to pip environment: {}".format(extra_url))

        self.worker_id = self._session.config["agent.worker_id"] or "{}:{}".format(
            self._session.config["agent.worker_name"], os.getpid()
        )
        self.parent_worker_id = None  # maybe add os env for overriding

        self.temp_config_path = None
        self.queues = ()
        self.venv_folder = None  # type: Optional[Text]
        self.package_api = None  # type: Optional[PackageManager]
        self.global_package_api = None

        self.poetry = PoetryConfig(self._session)
        self.uv = UvConfig(self._session)
        self.docker_image_func = None
        self._docker_image = None
        self._docker_arguments = None
        # if True, docker default passed on command line, which means we ignore the default docker match rules
        self._docker_default_cmd_override = False
        PackageManager.set_pip_version(self._session.config.get("agent.package_manager.pip_version", None))
        self._extra_docker_arguments = ENV_EXTRA_DOCKER_ARGS.get() or self._session.config.get(
            "agent.extra_docker_arguments", None
        )
        self._extra_shell_script = self._session.config.get("agent.extra_docker_shell_script", None)
        self._docker_force_pull = self._session.config.get("agent.docker_force_pull", False)
        self._daemon_foreground = None
        self._standalone_mode = None
        self._services_mode = None
        self._worker_tags = None
        self._force_current_version = None
        self._redirected_stdout_file_no = None
        self._suppress_cr = self._session.config.get("agent.suppress_carriage_return", True)
        self._host_ssh_cache = None
        self._truncate_task_output_files = bool(self._session.config.get("agent.truncate_task_output_files", False))
        self._use_podman = False
        self._run_mode = None  # "docker", "venv" or "host", set when the daemon starts

        # True - supported
        # None - not initialized
        # str - not supported, version string indicates last server version

        # allow docker sanitization, needs backend support
        if ENV_DOCKER_ARGS_FILTERS.get():
            self._docker_args_filters = [re.compile(f) for f in shlex.split(ENV_DOCKER_ARGS_FILTERS.get())]
        elif self._session.config.get("agent.docker_args_filters", None):
            self._docker_args_filters = [
                re.compile(f) for f in self._session.config.get("agent.docker_args_filters", [])
            ]
        else:
            self._docker_args_filters = []

        self._task_ping_interval_sec = max(
            0, text_to_int(self._session.config.get("agent.task_ping_interval_sec", 60.0))
        )

    @classmethod
    def _verify_command_states(cls, kwargs):
        """
        Conform and enforce command argument
        This is where you can automatically turn on/off switches based on different states.
        :param kwargs:
        :return: kwargs
        """
        if kwargs.get("services_mode"):
            kwargs["cpu_only"] = True
            # kwargs['docker'] = kwargs.get('docker') or []
            kwargs["gpus"] = None

        return kwargs

    def _get_requirements_manager(self, os_override=None, base_interpreter=None, requirement_substitutions=None):
        requirements_manager = RequirementsManager(self._session, base_interpreter=base_interpreter)
        requirement_substitutions = requirement_substitutions or self._requirement_substitutions
        for requirement_cls in requirement_substitutions:
            if os_override and issubclass(requirement_cls, PytorchRequirement):
                requirement_cls = partial(requirement_cls, os_override=os_override)
            requirements_manager.register(requirement_cls)
        if not requirements_manager.found_cuda:
            self.warning("could not find installed CUDA/CuDNN version using original requirements file")
        return requirements_manager

    def handle_user_abort(self, task_id, session=None):
        """
        Set task status to appropriate value on user abort.
        """
        session = session or self._session
        try:
            task_status = session.send_api(tasks_api.GetByIdRequest(task_id)).task.status
            if task_status == tasks_api.TaskStatusEnum.in_progress:
                print("\nUser abort - stopping task {}".format(task_id))
                session.send_api(tasks_api.StoppedRequest(task_id))
        except Exception:
            pass

    def _get_job_token(self, task_id):
        """The job's token and its lifetime, or (None, None) when it cannot be issued."""
        # noinspection PyBroadException
        try:
            return request_job_token(self._session, task_id) or (None, None)
        except Exception as ex:
            print("Warning: failed getting a job token for task '{}': {}".format(task_id, ex))
            return None, None

    def _get_docker_restart_value(self, task_session, task_id: str):
        try:
            self._session.verify_feature_set("advanced")
        except ValueError:
            return

        restart = (ENV_SERVICES_DOCKER_RESTART.get() or "").strip()
        if not restart:
            return

        # Parse value and selector
        restart_value, _, selector = restart.partition(";")

        if restart_value not in ("unless-stopped", "no", "always") and not restart_value.startswith("on-failure"):
            self.log.error(
                'Invalid value "{}" provided for {}, ignoring'.format(restart, ENV_SERVICES_DOCKER_RESTART.vars[0])
            )
            return

        if not selector:
            return restart_value

        path, _, expected_value = selector.partition("=")

        try:
            result = task_session.send_api(
                task_session.current(tasks_api.GetAllRequest)(id=[task_id], only_fields=[path], search_hidden=True)
            ).to_dict()
        except APIError as ex:
            self.log.error(
                'Failed obtaining selector value for restart option "{}", ignoring: {}'.format(selector, ex.message)
            )
            return

        not_found = object()
        try:
            value = self._get_path(result, "tasks", 0, *path.split("."), default=not_found)
        except (ValueError, TypeError):
            return

        if value is not_found:
            return

        if not expected_value:
            return restart_value

        # noinspection PyBroadException
        try:
            if (
                (isinstance(value, bool) and value == strtobool(expected_value))  # check first - bool is also an int
                or (isinstance(value, (int, float)) and value == float(expected_value))
                or (str(value) == str(expected_value))
            ):
                return restart_value
        except Exception:
            pass

    def run_one_task(self, queue, task_id, worker_args, docker=None, task_session=None):
        # type: (Text, Text, WorkerParams, Optional[Text], Optional[Session]) -> Optional[int]
        """
        Run one task pulled from queue.
        :param queue: ID of queue that task was pulled from
        :param task_id: ID of task to run
        :param worker_args: Worker command line arguments
        :param task_session: The session for running operations on the passed task
        :param docker: Docker image in which the execution task will run

        :return: exit code (0 is success)
        """
        # start new process and execute task id
        # "Running task '{}'".format(task_id)
        print(self._task_logging_start_message.format(task_id))
        task_session = task_session or self._session

        # noinspection PyBroadException
        try:
            result = task_session.send_api(
                task_session.current(tasks_api.GetAllRequest)(id=[task_id], only_fields=["runtime"], search_hidden=True)
            ).to_dict()

            runtime = result.get("tasks", [])[0].get("runtime") or {}
            runtime[self.hostname_task_runtime_prop] = socket.gethostname()

            task_session.send_api(
                task_session.current(tasks_api.EditRequest)(task=task_id, force=True, runtime=runtime)
            )
        except Exception as ex:
            print("Warning: failed obtaining/setting hostname for task '{}': {}".format(task_id, ex))

        # set task status to in_progress so we know it was popped from the queue
        if not self._get_node_rank():
            # noinspection PyBroadException
            try:
                task_session.send_api(
                    tasks_api.StartedRequest(task=task_id, status_message="launch by agent", force=True)
                )
            except Exception:
                print("Warning: Could not set status=in_progress task id '{}', skipping".format(task_id))
                return

        # the job's own token: the server issues it to the agent device that started the task
        job_token, job_token_lifetime = self._get_job_token(task_id)
        if not job_token:
            print("Warning: could not get a job token for task '{}', skipping".format(task_id))
            return
        # the token lives in a file the job reads, and the agent renews it while the job runs
        job_token_dir = Path(mkdtemp(prefix="cairnml_job_token_"))
        job_token_file = job_token_dir / TOKEN_FILE_NAME
        write_job_token(job_token_file, job_token)

        # setup console log
        temp_stdout_name = safe_mkstemp(
            suffix=".txt", prefix=".cairnml_agent_out.", name_only=True, dir=(ENV_TEMP_STDOUT_FILE_DIR.get() or None)
        )
        # temp_stderr_name = safe_mkstemp(suffix=".txt", prefix=".cairnml_agent_err.", name_only=True)
        temp_stderr_name = None
        print(
            "Storing stdout and stderr log to '{}', '{}'".format(temp_stdout_name, temp_stderr_name or temp_stdout_name)
        )

        docker_image = None
        worker_id = "{}:service:{}".format(self.worker_id, task_id) if self._services_mode else self.worker_id

        if self.docker_image_func:
            # noinspection PyBroadException
            try:
                task_container = get_task_container(
                    task_session, task_id, ignore_match_rules=self._docker_default_cmd_override
                )
            except Exception:
                task_container = {}

            default_docker = self._session.config.get("agent.disable_task_docker_override", False) or not bool(
                task_container.get("image")
            )
            if default_docker:
                docker_image = self._docker_image
                docker_arguments = self._docker_arguments
            else:
                docker_image = task_container.get("image") or self._docker_image
                docker_arguments = task_container.get("arguments", self._docker_arguments if default_docker else None)

            docker_setup_script = task_container.get("setup_shell_script")

            self.send_logs(
                task_id=task_id,
                lines=[
                    "Running Task {} inside {}docker: {} arguments: {}\n".format(
                        task_id,
                        "default " if default_docker else "",
                        docker_image,
                        DockerArgsSanitizer.sanitize_docker_command(self._session, docker_arguments or []),
                    )
                ]
                + (["custom_setup_bash_script:\n{}\n".format(docker_setup_script)] if docker_setup_script else []),
                level="INFO",
                session=task_session,
            )

            # Update docker command
            docker_params = dict(
                docker_image=docker_image,
                docker_arguments=docker_arguments,
                docker_bash_setup_script=docker_setup_script,
                restart=self._get_docker_restart_value(task_session, task_id),
            )
            docker_params["auth_token"] = job_token
            docker_params["job_token_dir"] = str(job_token_dir)
            if self._worker_tags:
                docker_params["worker_tags"] = self._worker_tags
            if self._services_mode:
                # if this is services mode, give the docker a unique worker id, as it will register itself.
                docker_params["worker_id"] = worker_id

            name_format = self._session.config.get("agent.docker_container_name_format", None)
            if name_format:
                custom_fields = {}
                name_format_fields = self._session.config.get("agent.docker_container_name_format_fields", None)
                if name_format_fields:
                    field_values = get_task_fields(task_session, task_id, name_format_fields.values(), log=self.log)
                    custom_fields = {k: field_values.get(v) for k, v in name_format_fields.items()}

                try:
                    name = name_format.format(
                        task_id=re.sub(r"[^a-zA-Z0-9._-]", "-", task_id),
                        worker_id=re.sub(r"[^a-zA-Z0-9._-]", "-", worker_id),
                        rand_string="".join(sys_random.choice(string.ascii_lowercase) for _ in range(32)),
                        **custom_fields,
                    )
                except Exception as ex:
                    print("Warning: failed generating docker container name: {}".format(ex))
                else:
                    if self._valid_docker_container_name(name):
                        docker_params["name"] = name
                    else:
                        print("Warning: generated docker container name is invalid: {}".format(name))

            # convert template arguments now (i.e. ${CAIRNML_} ), this is important for the docker arg
            # resolve the Task's docker arguments before everything else, because
            # unlike the vault/config these are not running as the agent's user, they are the user's,
            # we need to filter them post template parsing limitation to happen before the `docker_image_func` call
            docker_args_template_resolver = DockerArgsTemplateResolver(
                task_session=self._session,
                task_id=task_id,
                queue_id=queue,
                # todo cache with timout invalidation?!
                queue_name=self._resolve_id(id=queue, service="queues", search_hidden=True),
                worker_id=worker_id,
                task_info=None,
            )
            if docker_params.get("docker_arguments"):
                docker_params["docker_arguments"] = docker_args_template_resolver.resolve_from_docker_cmd(
                    full_docker_cmd=docker_params["docker_arguments"]
                )

            full_docker_cmd = self.docker_image_func(env_task_id=task_id, **docker_params)

            # if we are using the default docker, update back the Task:
            if default_docker:
                # noinspection PyBroadException
                try:
                    set_task_container(
                        task_session,
                        task_id=task_id,
                        docker_image=docker_image,
                        docker_arguments=docker_arguments,
                        docker_bash_script=docker_setup_script,
                    )
                except Exception:
                    pass

            # convert template arguments now (i.e. ${CAIRNML_} )
            # Notice we do not parse the last part of the docker cmd because that's
            # the actual command to be executed inside the docker
            full_docker_cmd = docker_args_template_resolver.resolve_from_docker_cmd(
                full_docker_cmd=full_docker_cmd[:-1]
            ) + [full_docker_cmd[-1]]

            # if this is services_mode, change the worker_id to a unique name
            # abd use full-monitoring, ot it registers itself as a worker for this specific service.
            # notice, the internal agent will monitor itself once the docker is up and running
            full_docker_cmd[-1] = full_docker_cmd[-1] + "execute {} {} --id {}".format(
                "--full-monitoring" if self._services_mode else "--disable-monitoring",
                "--standalone-mode" if self._standalone_mode else "",
                task_id,
            )

            display_docker_command = DockerArgsSanitizer.sanitize_docker_command(self._session, full_docker_cmd)

            # send the actual used command line to the backend
            self.send_logs(
                task_id=task_id,
                lines=["Executing: {}\n".format(display_docker_command)],
                level="INFO",
                session=task_session,
            )

            cmd = Argv(*full_docker_cmd, display_argv=display_docker_command)

            print("Running Docker:\n{}\n".format(str(cmd)))
        else:
            cmd = worker_args.get_argv_for_command("execute") + (
                "--full-monitoring" if self._services_mode else "--disable-monitoring",
                "--id",
                task_id,
            )

        events_service = self.get_service(Events)
        stop_signal = TaskStopSignal(
            command=self,
            session=task_session,
            events_service=events_service,
            task_id=task_id,
        )
        stop_signal_status = TaskStopSignal.default
        status = ExitStatus.interrupted
        renewer = None
        try:
            # set WORKER ID
            ENV_WORKER_ID.set(worker_id)

            if self._docker_force_pull and docker_image:
                full_pull_cmd = ["docker", "pull", docker_image]
                pull_cmd = Argv(*full_pull_cmd)
                status, stop_signal_status = self._log_command_output(
                    task_id=task_id,
                    cmd=pull_cmd,
                    stdout_path=temp_stdout_name,
                    stderr_path=temp_stderr_name,
                    daemon=True,
                    stop_signal=stop_signal,
                    session=task_session,
                )

            env = job_environment(
                os.environ,
                job_token=job_token,
                job_token_file=job_token_file,
            )
            if self._worker_tags:
                env[ENV_WORKER_TAGS.vars[0]] = " ".join(shlex.quote(t) for t in self._worker_tags)

            renewer = TokenRenewer(
                job_token_file,
                lambda: request_job_token(self._session, task_id),
                job_token_lifetime,
                label=task_id,
            )
            renewer.start()
            status, stop_signal_status = self._log_command_output(
                task_id=task_id,
                cmd=cmd,
                stdout_path=temp_stdout_name,
                stderr_path=temp_stderr_name,
                daemon=True,
                stop_signal=stop_signal,
                env=env,
                session=task_session,
            )
            errors = temp_stderr_name and Path(temp_stderr_name).read_text()
            if errors:
                print("\nEncountered errors:\n\n{}\n".format(errors))
            if status is None and self._services_mode:
                print("Service bootstrap completed: task '{}'".format(task_id))
            elif status is None:
                print("DONE: Running task '{}' (user aborted)".format(task_id))
            else:
                print("DONE: Running task '{}', exit status {}".format(task_id, status))
        except KeyboardInterrupt:
            self.handle_user_abort(task_id, session=task_session)
            status = ExitStatus.interrupted
        finally:
            # a service keeps running in the background after this call, with its token file and its renewal
            keep_token = self._services_mode and stop_signal_status is None
            if renewer and not keep_token:
                renewer.stop()
            if not keep_token:
                shutil.rmtree(job_token_dir, ignore_errors=True)
            if self._services_mode and stop_signal_status is None:
                print("Service started, docker running in the background")
            else:
                self.handle_task_termination(task_id, status, stop_signal_status, session=task_session)
                # remove temp files after we sent everything to the backend
                if self.docker_image_func:
                    shutdown_docker_process(docker_cmd_contains="--id {}'\"".format(task_id))
                safe_remove_file(temp_stdout_name)
                safe_remove_file(temp_stderr_name)
                if self._services_mode and status == ExitStatus.interrupted:
                    # unregister this worker, it was killed
                    self._unregister()

        return status

    @staticmethod
    def _queue_is_pulled_from(queue):
        """A queue tagged force_workers:off is not pulled from by this worker; any other queue is."""
        return QUEUE_TAG_OFF not in queue["tags"]

    def _get_queue_tags(self, queue_ids):
        return {
            q.id: {"name": q.name, "tags": q.tags}
            for q in self._session.send_api(queues_api.GetAllRequest(id=queue_ids, only_fields=["id", "tags"])).queues
        }

    def run_tasks_loop(self, queues, worker_params, priority_order=True):
        """
        :summary: Pull and run tasks from queues.
        :description: 1. Go through ``queues`` by order.
                      2. Try getting the next task for each and run the first one that returns.
                      3. Go to step 1
        :param list(str) queues: IDs of queues to pull tasks from
        :param worker_params worker_params: Worker command line arguments
        :param bool priority_order: If True pull order in priority manner. always from the first
            If False, pull from each queue once in a round robin manner
        """

        if not self._daemon_foreground:
            print("Starting infinite task polling loop...")

        if not self.monitor:
            self.new_monitor()

        # set when a task is pulled: the worker is then unregistered when the loop ends
        pulled_task = False
        try:
            while True:
                # iterate over queues (priority style, queues[0] is highest)
                queue_tags = self._get_queue_tags(queues)
                for queue in queues:
                    if not self._queue_is_pulled_from(queue_tags[queue]):
                        continue

                    # get next task in queue
                    try:
                        response = get_next_task(self._session, queue=queue)
                    except Exception as e:
                        print("Warning: Could not access task queue [{}], error: {}".format(queue, e))
                        continue
                    else:
                        try:
                            task_id = response["entry"]["task"]
                        except (KeyError, TypeError, AttributeError):
                            if self._daemon_foreground or worker_params.debug:
                                print("No tasks in queue {}".format(queue))
                            continue

                        pulled_task = True

                        # clear output log if we start a new Task
                        if (
                            not worker_params.debug
                            and self._redirected_stdout_file_no is not None
                            and self._redirected_stdout_file_no > 2
                        ):
                            # noinspection PyBroadException
                            try:
                                os.lseek(self._redirected_stdout_file_no, 0, 0)
                                os.ftruncate(self._redirected_stdout_file_no, 0)
                            except Exception:
                                pass

                        # set task status to in_progress so we know it was popped from the queue
                        # next api version we will set the status when pulling from the queue
                        if not self._get_node_rank():
                            # noinspection PyBroadException
                            try:
                                self._session.send_api(
                                    tasks_api.StartedRequest(task=task_id, status_message="pulled by agent", force=True)
                                )
                            except Exception:
                                print(
                                    "Warning: Could not set status=in_progress task id '{}', retrying in a bit".format(
                                        task_id
                                    )
                                )

                        task_session = None
                        self.report_monitor(ResourceMonitor.StatusReport(queues=queues, queue=queue, task=task_id))

                        self.send_logs(
                            task_id=task_id,
                            lines=["task {} pulled from {} by worker {}\n".format(task_id, queue, self.worker_id)],
                            level="INFO",
                            session=task_session,
                        )

                        self.run_one_task(queue, task_id, worker_params, task_session=task_session)

                        self.report_monitor(ResourceMonitor.StatusReport(queues=self.queues))

                        # if we are using priority start pulling from the first always,
                        # if we are doing roundrobin, pull from the next one
                        if priority_order:
                            break
                else:
                    # sleep and retry polling
                    if self._daemon_foreground or worker_params.debug:
                        print("No tasks in Queues, sleeping for {:.1f} seconds".format(self._polling_interval))
                    sleep(self._polling_interval)

                if self._session.config.get("agent.reload_config", False):
                    self.reload_config()
        finally:
            if not self.docker_image_func:
                # if we are in services mode, we should send termination signal to all child processes
                if self._services_mode:
                    terminate_all_child_processes(timeout=20, include_parent=False)

                # if we are here, just kill all sub processes
                kill_all_child_processes(debug=self._session.debug_mode if self._session else False)

            # unregister the worker, if we were terminated after pulling a Task
            if pulled_task:
                self._unregister()

    def reload_config(self):
        try:
            reloaded = self._session.config.reload()
        except Exception as ex:
            self.log("Failed reloading config file")
            self.log_traceback(ex)
        else:
            if reloaded:
                self.log('Config file change detected, reloading and updating "{.temp_config_path}"'.format(self))
                self.dump_config(self.temp_config_path)

    def check(self, **_):
        try:
            check_directory_path(str(Path(".").resolve()), check_whitespace_in_path=False)
        except OSError as e:
            if e.errno == errno.ENOENT:
                raise CommandFailedError("current working directory does not exist") from e
            raise

        for key in "agent.venvs_dir", "sdk.storage.cache.default_base_dir":
            try:
                value = self._session.config.get(key, None)
                if value:
                    check_directory_path(value)
            except CommandFailedError as e:
                raise CommandFailedError('Invalid config key "{}": {.message}'.format(key, e)) from e

        self._session.print_configuration()

    def resolve_daemon_queue_names(self, queues):
        return self._resolve_queue_names(queues=queues)

    def _server_is_loopback(self):
        """True when the API server is on this machine's loopback address (127.0.0.1, ::1 or localhost)."""
        from urllib.parse import urlparse

        host = urlparse(self._session.config.get("api.api_server", "") or "").hostname
        return host in ("localhost", "127.0.0.1", "::1")

    def daemon(self, queues, log_level, foreground=False, docker=False, detached=False, order_fairness=False, **kwargs):
        use_docker = docker not in (False, None)
        allow_venv = bool(kwargs.get("allow_venv", False))

        podman = kwargs.get("podman", None)
        self._use_podman = podman not in (False, None)

        if self._use_podman:
            # We need to potentially turn on docker support
            if not use_docker:
                use_docker = True
                docker = podman
            elif podman is not True and docker is not True and podman != docker:
                # both specify container and/or args, raise an error since they might be different
                raise ValueError(
                    "Running in docker+podman mode, different containers provided. "
                    "Please use --docker or --podman with container details, not both."
                )

        venv_warning = check_execution_mode(use_docker, allow_venv)
        if venv_warning:
            print(venv_warning)
        self._run_mode = run_mode_for(use_docker, allow_venv)
        # only the agent's owner may read its folder, which holds the device key
        secure_folder(DEVICE_KEY_PATH.parent)

        # We use containers with podman - this is identical to
        # check that we have docker command if we need it
        if use_docker and not check_if_command_exists("docker"):
            raise ValueError("Running in Docker mode, 'docker' command was not found")

        self.is_daemon = True

        self._worker_tags = kwargs.get("child_report_tags", None)

        self._standalone_mode = kwargs.get("standalone_mode", False)
        self._polling_interval = ENV_QUEUE_POLL_FREQ_SEC.get() or max(kwargs.get("polling_interval", 5), 5)
        self._services_mode = kwargs.get("services_mode", False)
        # must have docker in services_mode
        if self._services_mode:
            kwargs = self._verify_command_states(kwargs)
        # We are not running a daemon we are killing one.
        # find the pid send termination signal and leave
        if kwargs.get("stop", False) is not False:
            return_code = 0
            for worker_id in kwargs.get("stop") or [None]:
                if not self._kill_daemon(worker_id=worker_id):
                    return_code = 1
            return return_code

        # if we do not need to create queues, make sure they are valid
        # match previous behaviour when we validated queue names before everything else
        queues = self.resolve_daemon_queue_names(queues)

        queues_info = [q.to_dict() for q in self._session.send_api(queues_api.GetAllRequest(id=queues)).queues]

        # make sure we only have a single instance,
        # also make sure we set worker_id properly and cache folders
        self._singleton()

        # check if we have bootstrap package installed
        if bootstrap.check_bootstrap_package_install(config=self._session.config):
            check_latest = kwargs.get("check_bootstrap", False) or self._session.config.get(
                "agent.bootstrap.check_for_latest", False
            )
            if check_latest:
                bootstrap.check_latest_bootstrap_version(
                    timeout=self._session.config.get("agent.bootstrap.check_for_latest_timeout", 5)
                )
            if bootstrap.update_bootstrap(config=self._session.config):
                self._bootstrap = True
        else:
            bootstrap.print_bootstrap_missing_notice()

        self.check(**kwargs)
        self.log.debug("starting resource monitor thread")
        print('Worker "{}" - '.format(self.worker_id), end="")

        columns = ("id", "name", "tags")
        print("Listening to queues:")
        print_table(queues_info, columns=columns, titles=columns)

        # register worker
        self._register(queues)

        # create temp config file with current configuration
        self.temp_config_path = NamedTemporaryFile(suffix=".cfg", prefix=".cairnml_agent.", mode="w+t").name

        # print docker image
        if use_docker:
            self._force_current_version = kwargs.get("force_current_version", False)
            self.set_docker_variables(docker)
        else:
            self.dump_config(self.temp_config_path)
            # only in none docker we have to make sure we have CUDA setup

            # make sure we have CUDA set if we have --gpus
            if kwargs.get("gpus") and self._session.config.get("agent.cuda_version", None) in (None, 0, "0"):
                message = (
                    "Running with GPUs but no CUDA version was detected!\n"
                    "\tSet OS environment CUDA_VERSION & CUDNN_VERSION to the correct version\n"
                    "\tExample: export CUDA_VERSION=10.1 or (Windows: set CUDA_VERSION=10.1)"
                )
                if is_conda(self._session.config):
                    self._unregister(queues)
                    safe_remove_file(self.temp_config_path)
                    raise ValueError(message)
                else:
                    warning(message + "\n")

        if self._services_mode:
            print("CairnML-Agent running in services mode")

        self._daemon_foreground = foreground
        if not foreground:
            out_file, name = safe_mkstemp(
                prefix=".cairnml_agent_daemon_out",
                suffix=".txt",
                open_kwargs={"buffering": self._session.config.get("agent.log_files_buffering", 1)},
                dir=(ENV_TEMP_STDOUT_FILE_DIR.get() or None),
                mode="a",
            )
            print("Running CAIRNML-AGENT daemon in background mode, writing stdout/stderr to {}".format(name))

            if not self._session.debug_mode:
                self._temp_cleanup_list.append(name)

            # on widows we do nothing
            if detached and is_windows_platform():
                print("Detached not supported on Windows, ignoring --detached")
                detached = False

            if not detached:
                # redirect std out/err to new file
                sys.stdout = sys.stderr = out_file
            else:
                # in detached mode
                # fully detach stdin.stdout/stderr and leave main process, running in the background
                daemonize_process(out_file.fileno())
                self._redirected_stdout_file_no = out_file.fileno()
                # make sure we update the singleton lock file to the new pid
                Singleton.update_pid_file()
                # reprint headers to std file (we are now inside the daemon process)
                print('Worker "{}" :'.format(self.worker_id))
                self._session.print_configuration()
                print_table(queues_info, columns=columns, titles=columns)

        try:
            while True:
                try:
                    self.new_monitor(ResourceMonitor.StatusReport(queues=queues))
                    self.run_tasks_loop(
                        queues,
                        worker_params=WorkerParams(
                            log_level=log_level,
                            config_file=self.temp_config_path,
                            debug=self._session.debug_mode,
                            trace=self._session.trace,
                        ),
                        priority_order=not order_fairness,
                    )
                except Exception as e:
                    tb = str(traceback.format_exc())
                    print("FATAL ERROR:")
                    print(tb)

                    if self._session.config.get("agent.crash_on_exception", False):
                        raise e

                    crash_file, name = safe_mkstemp(
                        prefix=".cairnml_agent-crash", suffix=".log", dir=(ENV_TEMP_STDOUT_FILE_DIR.get() or None)
                    )
                    try:
                        with crash_file:
                            crash_file.write(tb)
                    except Exception:
                        print("Could not write crash log to {}\nException:\n{}".format(name, tb))
                    sleep(1)
        finally:
            self._unregister(queues)
            safe_remove_file(self.temp_config_path)

    def report_monitor(self, report):
        if not self.monitor:
            self.new_monitor(report=report)
        else:
            self.monitor.set_report(report)
        self.monitor.send_report()

    def stop_monitor(self):
        if self.monitor:
            self.monitor.stop()
            self.monitor = None

    def new_monitor(self, report=None):
        self.stop_monitor()
        self.monitor = ResourceMonitor(
            session=self._session,
            worker_id=self.worker_id,
            first_report_sec=3.0,
            report_frequency_sec=self._machine_update_interval,
            worker_tags=None if self._services_mode else self._worker_tags,
            run_mode=self._run_mode,
        )
        self.monitor.set_report(report)
        self.monitor.start()
        return self.monitor

    def dump_config(self, filename, config=None):
        """Writes the configuration of the agent (or of config) to filename; False when the file cannot be written.

        Only the file access is caught: a configuration that cannot be turned into a dictionary is an error of the
        agent and is raised, not reported as a file that could not be written.
        """
        try:
            current_content = Path(filename).read_text()
        except OSError:
            current_content = None

        config_data = self._session.config.as_plain_ordered_dict() if config is None else config.as_plain_ordered_dict()
        api = config_data.get("api")
        if api:
            # the job signs in with its own token, never with the agent's device
            api.pop("idp", None)

        new_content = str(json.dumps(config_data, cls=HOCONEncoder, indent=4))
        # Overwrite file only if the content is different, because we are mounting the same file  into
        # multiple containers in services mode, and we don't want to change it if we do not have to.
        if new_content != current_content:
            try:
                Path(filename).write_text(new_content)
            except OSError as ex:
                print(f"Could not write the configuration file {filename}: {ex}")
                return False
        return True

    def _log_command_output(
        self,
        task_id,  # type: Text
        cmd,  # type: Executable
        stdout_path=None,  # type: Text
        stderr_path=None,  # type: Optional[Text]
        daemon=False,  # type: bool
        cwd=None,  # type: Text
        stop_signal=None,  # type: Optional[TaskStopSignal]
        session=None,
        **kwargs,  # type: Any
    ):
        # type: (...) -> Tuple[Optional[int], Optional[TaskStopReason]]
        def _print_file(file_path, prev_pos=0):
            mode = "rb+" if self._truncate_task_output_files else "rb"
            # noinspection PyBroadException
            try:
                with open(file_path, mode) as f:
                    f.seek(prev_pos)
                    binary_text = f.read()
                    pos = f.tell()
                    if self._truncate_task_output_files:
                        # buffered - read everything and truncate
                        # noinspection PyBroadException
                        try:
                            # we must seek to the beginning otherwise truncate will add \00
                            f.seek(0)
                            # os level truncate because f.truncate will push \00 at the end of the file
                            os.ftruncate(f.fileno(), 0)
                            os.fsync(f.fileno())
                        except Exception:
                            pass
                        pos = 0
            except Exception:
                return [], prev_pos

            # skip the previously printed lines,
            blines = binary_text.split(b"\n") if binary_text else []
            if not blines:
                return blines, pos
            return (
                decode_binary_lines(
                    blines if blines[-1] else blines[:-1],
                    replace_cr=not self._suppress_cr,
                    overwrite_cr=self._suppress_cr,
                ),
                pos,
            )

        safe_remove_file(stdout_path)
        stdout = open(stdout_path, "at")
        if stderr_path:
            safe_remove_file(stderr_path)
            stderr = open(stderr_path, "at")
        else:
            stderr = stdout
        stdout_line_count, stdout_pos_count = 0, 0
        stderr_line_count, stderr_pos_count = 0, 0
        lines_buffer = defaultdict(list)

        def report_lines(lines, source, a_multi_node_single_task=None):
            # support colored multi-node reporting on the same Task for easier debugging
            if lines and a_multi_node_single_task and a_multi_node_single_task > 0:
                rank = self._get_node_rank()
                if rank:
                    # see ANSI color: https://en.wikipedia.org/wiki/ANSI_escape_code#8-bit
                    # Only the "RANK x: line is colored to preserve the original color reporting
                    lines = ["\033[38;5;{}mRANK {}:\033[0m\n".format(20 + (rank % 210), rank)] + lines

            if not self._truncate_task_output_files:
                # non-buffered
                return self.send_logs(task_id, lines, session=session)

            buffer = lines_buffer[source]
            buffer += lines

            sent = self.send_logs(task_id, buffer, session=session)
            if sent > 0:
                lines_buffer[source] = buffer[sent:]
            return sent

        service_mode_internal_agent_started = None
        stopping = False
        status = None
        process = None
        last_task_ping = 0
        multi_node_single_task = ENV_MULTI_NODE_SINGLE_TASK.get()
        try:
            stop_reason = None

            process = cmd.call_subprocess(
                subprocess.Popen, stdout=stdout, stderr=stderr, cwd=cwd and str(cwd), **kwargs
            )

            while status is None and not stopping:
                stop_reason = stop_signal.test() if stop_signal else TaskStopSignal.default
                if stop_reason != TaskStopSignal.default:
                    # mark quit loop
                    stopping = True
                    if daemon:
                        self.send_logs(
                            task_id=task_id,
                            lines=[
                                "{}: stopping task ({}) {}\n".format(
                                    "User aborted" if stop_reason != TaskStopReason.exception else "Task failed",
                                    stop_reason,
                                    TaskStopReason.to_str(stop_reason),
                                )
                            ],
                            level="ERROR",
                            session=session,
                        )
                        kill_all_child_processes(
                            process.pid, debug=self._session.debug_mode if self._session else False
                        )
                else:
                    sleep(self._polling_interval)
                    status = process.poll()
                # flush stdout and stderr buffers
                if stdout:
                    stdout.flush()
                if stderr:
                    stderr.flush()

                if (
                    not stopping
                    and self._task_ping_interval_sec
                    and time() - last_task_ping > self._task_ping_interval_sec
                ):
                    # noinspection PyBroadException
                    try:
                        res = (session or self._session).send(tasks_api.PingRequest(task=task_id))
                        if not res:
                            self.log.error("Failed sending ping for task %s: %s", task_id, res.response)
                    except Exception as ex:
                        self.log.error("Failed sending ping: %s", str(ex))
                    finally:
                        last_task_ping = time()

                # get diff from previous poll
                printed_lines, stdout_pos_count = _print_file(stdout_path, stdout_pos_count)
                if self._services_mode and not stopping and status is None:
                    # if the internal agent started, we stop logging, it will take over logging.
                    # if the internal agent started running the task itself, it will return status==0,
                    # then we can quit the monitoring loop of this process
                    printed_lines, service_mode_internal_agent_started, status = self._check_if_internal_agent_started(
                        printed_lines, service_mode_internal_agent_started, task_id
                    )
                    if status is not None:
                        stop_reason = "Service started"

                stdout_line_count += report_lines(printed_lines, "stdout", multi_node_single_task)

                if stderr_path:
                    printed_lines, stderr_pos_count = _print_file(stderr_path, stderr_pos_count)
                    stderr_line_count += report_lines(printed_lines, "stderr", multi_node_single_task)

        except subprocess.CalledProcessError as ex:
            # non zero return code
            stop_reason = TaskStopReason.exception
            status = ex.returncode
        except KeyboardInterrupt:
            # so someone else will catch us
            if process:
                kill_all_child_processes(process.pid, debug=self._session.debug_mode if self._session else False)
            raise
        except Exception:
            # we should not get here, but better safe than sorry
            printed_lines, stdout_pos_count = _print_file(stdout_path, stdout_pos_count)
            stdout_line_count += report_lines(printed_lines, "stdout", multi_node_single_task)
            if stderr_path:
                printed_lines, stderr_pos_count = _print_file(stderr_path, stderr_pos_count)
                stderr_line_count += report_lines(printed_lines, "stderr", multi_node_single_task)
            stop_reason = TaskStopReason.exception
            status = -1

        # if running in services mode, keep the file open
        # in case the docker was so quick it started and finished, check the stop reason
        if self._services_mode and service_mode_internal_agent_started and stop_reason == "Service started":
            return None, None

        # full cleanup (just in case)
        if process and not self._services_mode:
            kill_all_child_processes(process.pid)

        stdout.close()
        if stderr_path:
            stderr.close()

        # Send last lines
        printed_lines, stdout_pos_count = _print_file(stdout_path, stdout_pos_count)
        stdout_line_count += report_lines(printed_lines, "stdout")
        if stderr_path:
            printed_lines, stderr_pos_count = _print_file(stderr_path, stderr_pos_count)
            stderr_line_count += report_lines(printed_lines, "stderr")

        # make sure that if the abort function was called, the task is marked as aborted
        if stop_signal and stop_signal.was_abort_function_called(status):
            stop_reason = TaskStopReason.stopped

        # now we delete the temp files
        safe_remove_file(stdout_path)
        if stderr_path:
            safe_remove_file(stdout_path)

        return status, stop_reason

    def _check_if_internal_agent_started(self, printed_lines, service_mode_internal_agent_started, task_id):
        log_start_msg = self._task_logging_start_message.format(task_id)
        log_control_end_msg = self._task_logging_pass_control_message.format(task_id)
        filter_lines = printed_lines if not service_mode_internal_agent_started else []
        for i, line in enumerate(printed_lines):
            if not service_mode_internal_agent_started and line.startswith(log_start_msg):
                service_mode_internal_agent_started = True
                filter_lines = printed_lines[: i + 1]
            elif line.startswith(log_control_end_msg):
                service_mode_internal_agent_started = True
                return filter_lines, service_mode_internal_agent_started, 0

        return filter_lines, service_mode_internal_agent_started, None

    def send_logs(self, task_id, lines, level="DEBUG", session=None):
        """
        Send output lines as log events to backend
        :param task_id: ID of task to send logs for
        :type task_id: Text
        :param lines: lines to send
        :type lines: [Text]
        :param str level: log level, default DEBUG
        :return: number of lines sent
        :rtype: int
        """
        if not lines:
            return 0
        print_text("".join(lines), newline=False)

        # remove backspaces from the text log, they look bad.
        for i, l in enumerate(lines):
            lines[i] = l.replace("\x08", "")

        events_service = self.get_service(Events)
        try:
            events_service.send_log_events(self.worker_id, task_id=task_id, lines=lines, level=level, session=session)
            return len(lines)
        except Exception as e:
            print("\n### Error sending log: %s ###\n" % e)
            # revert number of sent lines (we will try next time)
            return 0

    def _update_commit_id(self, task_id, execution, repo_info):
        """
        If commit ID is not set, set it to the currently running version of the repository
        """
        if not repo_info.commit or execution.version_num:
            return
        self.log("Updating task commit ID: %s", repo_info.commit)
        try:
            edit_cls, _ = self._session.definition("tasks", "edit")
            self._session.send_api(
                edit_cls(task_id, script=dict(binary=None, version_num=repo_info.commit), force=True)
            )
        except Exception:
            pass

    def apply_diff(self, task, vcs, execution_info, repo_info):
        # type: (Any, VCS, ExecutionInfo, RepoInfo) -> None
        """
        Apply task diff if present
        """
        diff = task.script and task.script.diff
        if not diff:
            return
        print("Applying uncommitted changes")
        try:
            success = vcs.patch(normalize_path(repo_info.root), diff)
        except Exception as ex:
            self.log.warning("could not apply diff: %s", ex)
            success = False

        if not success:
            raise ValueError(
                "Failed applying git diff:\n{}\n\nERROR! Failed applying git diff, see diff above.".format(diff)
            )

    def _apply_extra_configuration(self):
        # store a few things we updated in runtime (TODO: we should list theme somewhere)
        config = self._session.config

        default = config.get("agent.apply_environment", False)
        if ENV_ENABLE_ENV_CONFIG_SECTION.get(default=default):
            try:
                keys = apply_environment(config)
                if keys:
                    print("Environment variables set from configuration: {}".format(keys))
            except Exception as ex:
                print("Error: failed applying environment from configuration: {}".format(ex))

        default = config.get("agent.apply_files", default=False)
        if ENV_ENABLE_FILES_CONFIG_SECTION.get(default=default):
            try:
                apply_files(config)
            except Exception as ex:
                print("Error: failed applying files from configuration: {}".format(ex))

    @resolve_names
    def build(
        self,
        task_id,
        target=None,
        python_version=None,
        docker=None,
        entry_point=None,
        install_globally=False,
        force_docker=False,
        **_,
    ):
        if not task_id:
            raise CommandFailedError("Worker build must have valid task id")

        if target and not os.path.isabs(target):
            # Non absolute target path will lead to errors with relative python executable
            target = os.path.abspath(target)

        self._session.print_configuration()

        if docker is not False and docker is not None:
            return self._build_docker(docker, target, task_id, entry_point, force_docker=force_docker)

        current_task = self._session.send_api(self._session.current(tasks_api.GetByIdRequest)(task_id)).task

        execution = self.get_execution_info(current_task)

        if self._session.config.get("agent.package_manager.force_repo_requirements_txt", False):
            requirements = None
            print(
                "[package_manager.force_repo_requirements_txt=true] "
                'Skipping requirements, using repository "requirements.txt" '
            )
        else:
            try:
                requirements = current_task.script.requirements
            except AttributeError:
                requirements = None

        if not python_version:
            python_version = self._get_task_python_version(current_task)

        venv_folder, requirements_manager, is_cached, requested_python_version = self.install_virtualenv(
            venv_dir=target,
            requested_python_version=python_version,
            execution_info=execution,
            cached_requirements=requirements,
        )

        if not is_cached and self._default_pip:
            if install_globally and self.global_package_api:
                self.global_package_api.install_packages(*self._default_pip)
            else:
                self.package_api.install_packages(*self._default_pip)

        directory, vcs, repo_info = self.get_repo_info(execution, current_task, venv_folder.as_posix())

        cwd = vcs.location if vcs and vcs.location else directory

        if is_cached:
            # reinstalling git / local packages
            package_api = copy(self.package_api)
            OnlyExternalRequirements.cwd = package_api.cwd = cwd
            package_api.requirements_manager = self._get_requirements_manager(
                base_interpreter=package_api.requirements_manager.get_interpreter(),
                requirement_substitutions=[LocalSdkRequirement, CachedPackageRequirement, OnlyExternalRequirements],
            )
            # manually update the current state,
            # for the external git reference chance (in the replace callback)
            package_api.requirements_manager.update_installed_packages_state(package_api.freeze())
            # make sure we run the handlers
            cached_requirements = {
                k: package_api.requirements_manager.replace(
                    requirements=requirements[k] or "",
                    existing_packages=package_api.requirements_manager.get_installed_packages_state(),
                )
                for k in requirements
            }
            package_api.load_requirements(cached_requirements)
            # make sure we call the correct freeze
            requirements_manager = package_api.requirements_manager
        else:
            self.install_requirements(
                execution,
                repo_info,
                requirements_manager=requirements_manager,
                cached_requirements=requirements,
                cwd=cwd,
                package_api=self.global_package_api if install_globally else None,
                requested_python_version=requested_python_version,
            )

        freeze = self.freeze_task_environment(
            task_id=task_id, requirements_manager=requirements_manager, update_requirements=False
        )
        script_dir = directory

        # Summary
        print("Restoring running environment of task id [%s]:" % task_id)
        if freeze:
            print("Summary - installed python packages:")
            print(dump_flat_dict(freeze))
        else:
            print("No freeze information available")

        print("Virtual environment: {}".format(venv_folder / "bin"))
        print("Source code: {}".format(repo_info.root if repo_info else execution.entry_point))
        print("Entry point: {}".format(Path(script_dir) / execution.entry_point))

        return 0

    def _build_docker(self, docker, target, task_id, entry_point=None, force_docker=False):

        self.temp_config_path = safe_mkstemp(
            suffix=".cfg",
            prefix=".cairnml_agent.",
            text=True,
            name_only=True,
            dir=(ENV_TEMP_STDOUT_FILE_DIR.get() or None),
        )
        if not target:
            target = "task_id_{}".format(task_id)

        temp_config, docker_image_func = self.get_docker_config_cmd(docker)
        self.dump_config(self.temp_config_path, config=temp_config)
        self.docker_image_func = docker_image_func

        docker_image = self._docker_image
        docker_arguments = self._docker_arguments
        docker_setup_script = None

        if force_docker:
            print("Ignoring any task container info, using docker image {}".format(docker_image))
        else:
            # noinspection PyBroadException
            try:
                task_container = get_task_container(
                    self._session, task_id, ignore_match_rules=self._docker_default_cmd_override
                )
                if task_container.get("image") and not self._session.config.get(
                    "agent.disable_task_docker_override", False
                ):
                    docker_image = task_container.get("image")
                    print("Ignoring default docker image, using task docker image {}".format(docker_image))
                    docker_arguments = task_container.get("arguments")
                    docker_setup_script = task_container.get("setup_shell_script")
            except Exception:
                pass

        print(
            "Building Task {} inside docker image: {} {} setup_script={}\n".format(
                task_id, docker_image, docker_arguments or "", docker_setup_script or ""
            )
        )
        full_docker_cmd = self.docker_image_func(
            docker_image=docker_image, docker_arguments=docker_arguments, docker_bash_setup_script=docker_setup_script
        )

        # convert docker template arguments (i.e. ${CAIRNML_} ) based on the current Task
        docker_args_template_resolver = DockerArgsTemplateResolver(
            task_session=self._session, task_id=task_id, queue_id=None, queue_name=None, worker_id=None, task_info=None
        )
        full_docker_cmd = docker_args_template_resolver.resolve_from_docker_cmd(full_docker_cmd=full_docker_cmd)

        end_of_build_marker = "build.done=true"
        docker_cmd_suffix = (
            " build --id {task_id} --install-globally; "
            'ORG=$(stat -c "%u:%g" {conf_file}) ; chown $(whoami):$(whoami) {conf_file} ; '
            'echo "" >> {conf_file} ; echo {end_of_build_marker} >> {conf_file} ; '
            "chown $ORG {conf_file} ; "
            "bash".format(task_id=task_id, end_of_build_marker=end_of_build_marker, conf_file=DOCKER_ROOT_CONF_FILE)
        )
        full_docker_cmd[-1] = full_docker_cmd[-1] + docker_cmd_suffix
        cmd = Argv(*full_docker_cmd)

        # we will be checking the configuration file for changes
        temp_config = Path(self.temp_config_path)
        base_time_stamp = temp_config.stat().st_mtime

        # start the docker
        print("Starting docker build")
        cmd.call_subprocess(subprocess.Popen)

        # now we need to wait until the line shows on our configuration file.
        while True:
            # noinspection PyBroadException
            try:
                while temp_config.stat().st_mtime == base_time_stamp:
                    sleep(5.0)
                with open(temp_config.as_posix()) as f:
                    lines = [l.strip() for l in f.readlines()]
            except Exception:
                # print("Failed reading status file [{}], retrying in 2 seconds".format(ex))
                sleep(2.0)

            if "build.done=true" in lines:
                break
            base_time_stamp = temp_config.stat().st_mtime

        print("\nDocker build done")

        # get the docker id.
        docker_id = get_docker_id(docker_cmd_contains="--id {} ".format(task_id))
        if not docker_id:
            print("Error: cannot locate docker for storage")
            return

        if entry_point == "clone_task" or entry_point == "reuse_task":
            change = (
                'ENTRYPOINT if [ ! -s "{trains_conf}" ] ; then '
                "cp {default_trains_conf} {trains_conf} && export CAIRNML_CONFIG_FILE={trains_conf}; "
                " fi ; cairnml-agent execute --id {task_id} --standalone-mode {clone}".format(
                    default_trains_conf=DOCKER_DEFAULT_CONF_FILE,
                    trains_conf=DOCKER_ROOT_CONF_FILE,
                    task_id=task_id,
                    clone=("--clone" if entry_point == "clone_task" else ""),
                )
            )
        else:
            change = "ENTRYPOINT []"

        print("Committing docker container to: {}".format(target))
        print(commit_docker(container_name=target, docker_id=docker_id, apply_change=change))
        shutdown_docker_process(docker_id=docker_id)

        safe_remove_file(temp_config.as_posix())

        return

    @staticmethod
    def _get_task_python_version(task):
        # noinspection PyBroadException
        try:
            python_ver = task.script.binary
            python_ver = python_ver.split("/")[-1]
            if not python_ver.startswith("python"):
                return None

            python_ver = python_ver.replace("python", "")
            # if we can cast it, we are good
            parts = python_ver.split(".")
            if len(parts) < 2:
                return "{}".format(int(parts[0]))
            else:
                return "{}.{}".format(int(parts[0]), int(parts[1]))
        except Exception:
            return None

    @resolve_names
    def execute(
        self,
        task_id,
        log_level,
        optimization=0,
        disable_monitoring=False,
        full_monitoring=False,
        require_queue=False,
        log_file=None,
        standalone_mode=None,
        docker=False,
        clone=False,
        **_,
    ):
        self._standalone_mode = standalone_mode

        if not task_id:
            raise CommandFailedError("Worker execute must have valid task id")

        try:
            current_task = self._session.send_api(self._session.current(tasks_api.GetByIdRequest)(task_id)).task
            if not current_task.id:
                pass
        except AttributeError as ex:
            raise ValueError(
                "Could not find task id={} (for host: {})".format(task_id, self._session.config.get("api.host", ""))
            ) from ex
        except Exception as ex:
            raise ValueError(
                "Could not find task id={} (for host: {})\nException: {}".format(
                    task_id, self._session.config.get("api.host", ""), ex
                )
            ) from ex

        # check if we have bootstrap only if we are in docker mode
        if docker not in (False, None):
            if bootstrap.check_bootstrap_package_install(config=self._session.config) and bootstrap.update_bootstrap(
                config=self._session.config
            ):
                self._bootstrap = True

        # if we are in bootstrap compiled execution run, there is no actual python executable and env.
        # this means if we are using pip package manager, we need to set to UV so it installs python binaries.
        self._prepare_compiled_bootstrap(current_task)

        current_task = self._clone_or_dequeue(task_id, current_task, clone, require_queue)

        if docker is not False and docker is not None:
            self.set_docker_variables(docker)

        # We expect the same behavior in case full_monitoring was set, and in case docker mode is used

        if full_monitoring or docker is not False:
            return self._run_in_worker(current_task, task_id, log_level, docker, full_monitoring)

        self._session.print_configuration()

        # now mark the task as started
        self._session.send_api(
            self._session.current(tasks_api.StartedRequest)(
                task=current_task.id,
                status_reason="worker started execution",
                status_message=self._task_status_change_message,
                force=True,
            )
        )

        if not disable_monitoring:
            self.log.debug("starting resource monitor")
            self.report_monitor(ResourceMonitor.StatusReport(task=current_task.id))

        execution = self.get_execution_info(current_task)

        freeze, repo_info, script_dir, venv_folder = self._prepare_environment(current_task, execution, standalone_mode)

        # run code
        # print("Running task id [%s]:" % current_task.id)
        print(self._task_logging_pass_control_message.format(current_task.id))

        command = self._build_command(current_task, execution, script_dir, optimization, log_level)
        print("[{}]$ {}".format(execution.working_dir, command.pretty()))

        if freeze:
            print("Summary - installed python packages:")
            print(dump_flat_dict(freeze))
        else:
            print("No freeze information available")

        print("Environment setup completed successfully\n")

        # update the jobs global environment variable
        os.environ.update(self._get_job_os_envs(current_task, log_level))

        if repo_info:
            self._update_commit_id(current_task.id, execution, repo_info)

        # get Task Environments variables and update the process (if enabled)
        os.environ.update(self._get_task_os_env(self._session.config, current_task) or dict())

        # Add the script CWD to the python path
        conda = self.is_conda or self.uv.enabled
        if repo_info and repo_info.root and self._session.config.get("agent.force_git_root_python_path", None):
            python_path = get_python_path(repo_info.root, None, self.package_api, is_conda_env=conda)
        else:
            python_path = get_python_path(script_dir, execution.entry_point, self.package_api, is_conda_env=conda)
        if ENV_TASK_EXTRA_PYTHON_PATH.get():
            python_path = add_python_path(python_path, ENV_TASK_EXTRA_PYTHON_PATH.get())
        if python_path:
            os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, (os.environ.get("PYTHONPATH", None), python_path)))

        # check if we want to run as another user, only supported on linux
        # Note: User execution requires a virtual environment, so it's disabled when
        # ENV_AGENT_SKIP_PYTHON_ENV_INSTALL is set
        if ENV_TASK_EXECUTE_AS_USER.get() and is_linux_platform() and not ENV_AGENT_SKIP_PYTHON_ENV_INSTALL.get():
            command, script_dir = self._run_as_user_patch(
                command,
                self._session.config_file,
                script_dir,
                venv_folder,
                self._session.config.get("sdk.storage.cache.default_base_dir"),
                ENV_TASK_EXECUTE_AS_USER.get(),
            )
            use_execv = False
        else:
            use_execv = is_linux_platform() and not isinstance(self.package_api, (PoetryAPI, UvAPI, CondaAPI))

        self._session.send_api(
            self._session.current(tasks_api.StartedRequest)(
                task=current_task.id,
                status_reason="worker starting task execution",
                status_message=self._task_status_change_message,
                force=True,
            )
        )

        stop_signal = None
        if TaskStopSignal.check_registered_bash_callback():
            use_execv = False
            stop_signal = TaskStopSignal(
                command=self,
                session=self._session,
                events_service=self.get_service(Events),
                task_id=task_id,
                bash_cwd=script_dir,
            )
            stop_signal.start_monitor_thread(polling_interval_sec=self._polling_interval)

        # check if we need to add encoding to the subprocess
        if sys.getfilesystemencoding() == "ascii" and not os.environ.get("PYTHONIOENCODING"):
            os.environ["PYTHONIOENCODING"] = "utf-8"

        # remove our internal env
        os.environ.pop("CAIRNML_APT_INSTALL", None)
        # remove UV stuff, just in case
        os.environ.pop("UV_CACHE_DIR", None)
        os.environ.pop("UV_PYTHON_CACHE_DIR", None)
        os.environ.pop("UV_PYTHON_INSTALL_DIR", None)

        # if we are using a Command i.e. not execs update command env vars
        command.update_envs(os.environ)

        print("Starting Task Execution: {}\n".format(current_task.id))
        exit_code = self._run_command(
            current_task, command, script_dir, use_execv, disable_monitoring, log_file, stop_signal
        )

        return self._finish_run(current_task, exit_code, disable_monitoring, stop_signal)

    def _prepare_compiled_bootstrap(self, current_task):
        """Choose the Python and package manager for a compiled (bootstrap) agent before the task runs."""
        if "__compiled__" in globals():
            self._compiled = True
            print(
                "INFO: COMPILED BOOTSTRAP AGENT detected - Running as a compiled binary - Verifying or creating Python environment"
            )

            # Get the Task requested execution binary (e.g. "python3", "python3.9", "/usr/bin/python3.10")
            task_exec_binary = None
            try:
                task_exec_binary = (current_task.script.binary or "").strip()
                if task_exec_binary:
                    task_exec_binary = basename(task_exec_binary)
            except Exception:
                pass
            task_exec_binary = task_exec_binary or None

            # Check if task_exec_binary looks like a python version string
            # (e.g. "python", "python3", "python3.9", "python3.9.10")
            is_python_binary = bool(task_exec_binary and re.match(r"^python\d*(\.\d+)*$", task_exec_binary))
            if not is_python_binary:
                print(
                    f"INFO: Task binary '{task_exec_binary}' is not a Python version identifier, defaulting to 'python3' for bootstrapping"
                )
                task_exec_binary = "python3"

            print(
                f"INFO: Task requested execution binary: '{current_task.script.binary}', using '{task_exec_binary}' for bootstrapping"
            )

            # Check what package manager the user requested
            requested_pkg_manager = self._session.config.get("agent.package_manager.type", None)
            print(f"INFO: Configured package manager type: '{requested_pkg_manager or 'not specified'}'")

            # Try to find the requested Python version on the system
            print(f"INFO: Searching for Python executable matching '{task_exec_binary}'...")
            found_python = None
            found_python_version = None
            try:
                found_python_version, _, found_python = self.find_python_executable_for_version(
                    task_exec_binary, raise_on_error=False
                )
            except Exception:
                pass

            use_uv = True  # default to UV for compiled agent

            if requested_pkg_manager not in ("pip",):
                # UV (explicit or default): use the requested Python if found, otherwise UV will install it
                if not found_python:
                    print(
                        f"INFO: No Python executable found matching '{task_exec_binary}' - UV will provide Python and manage packages"
                    )
                else:
                    sys.executable = found_python
                    print(
                        f"INFO: Found Python executable: '{sys.executable}' (version {found_python_version or 'unknown'}), UV will use it"
                    )
            else:
                # User wants pip: find any available Python on the system (even if not the exact requested version)
                # find_python_executable_for_version already falls back progressively
                # (e.g. python3.10 -> python3 -> python), so found_python may differ from task_exec_binary
                if not found_python:
                    print("INFO: pip requested but no Python executable found on system, falling back to UV")
                    if self._session.config.get("agent.package_manager.uv_unsafe_best_match", None) is None:
                        self._session.config.put("agent.package_manager.uv_unsafe_best_match", True)
                        print("INFO: Enabling uv_unsafe_best_match for UV fallback")
                else:
                    sys.executable = found_python
                    print(
                        f"INFO: Found Python executable: '{sys.executable}' (version {found_python_version or 'unknown'})"
                    )
                    # Check if pip is actually available with this Python
                    print(f"INFO: pip requested, checking if pip is available with '{sys.executable}'...")
                    try:
                        pip_version = (
                            subprocess.check_output(
                                [sys.executable, "-m", "pip", "--version"], stderr=subprocess.DEVNULL
                            )
                            .decode("utf-8", errors="replace")
                            .strip()
                        )
                        use_uv = False
                        print(f"INFO: pip is available [{pip_version}], using pip as package manager")
                    except (subprocess.CalledProcessError, OSError, FileNotFoundError):
                        print(f"INFO: pip requested but not found with '{sys.executable}', falling back to UV")
                        if self._session.config.get("agent.package_manager.uv_unsafe_best_match", None) is None:
                            self._session.config.put("agent.package_manager.uv_unsafe_best_match", True)
                            print("INFO: Enabling uv_unsafe_best_match for UV fallback")

            if use_uv:
                self._session.config.put("agent.package_manager.type", "uv")
                # When using UV, do not allow it to revert to pip, we might not have it
                print("INFO: Package manager set to UV")

            self._session.config.put("agent.package_manager.uv_replace_pip", True)

            print(
                f"INFO: COMPILED BOOTSTRAP AGENT pre-setup initialization complete - "
                f"python='{sys.executable or 'none - Pending UV installation'}', "
                f"package_manager='{self._session.config.get('agent.package_manager.type', 'not set')}'"
            )

    def _clone_or_dequeue(self, task_id, current_task, clone, require_queue):
        """Clone the task (for a clone run), or move it out of the queue and mark it started."""
        if clone:
            try:
                print("Cloning task id={}".format(task_id))
                current_task = self._session.send_api(
                    self._session.current(tasks_api.GetByIdRequest)(
                        self._session.send_api(
                            tasks_api.CloneRequest(
                                task=current_task.id, new_task_name="Clone of {}".format(current_task.name)
                            )
                        ).id
                    )
                ).task
                print("Task cloned, new task id={}".format(current_task.id))
            except Exception as ex:
                raise CommandFailedError("Cloning failed") from ex
        else:
            # make sure this task is not stuck in an execution queue, it shouldn't have been, but just in case.
            # noinspection PyBroadException
            try:
                res = self._session.send(
                    self._session.current(tasks_api.DequeueRequest)(task=current_task.id, new_status="in_progress")
                )
                updated = (res.response_data or {}).get("updated", 0) if res.ok() else 0
                if require_queue and (not res.ok() or updated < 1):
                    raise ValueError(
                        "Execution required enqueued task, but task id={} is not queued.".format(current_task.id)
                    )
                # only force set started if we actually dequeued it (which would have changed the state)
                if res.ok() and updated:
                    # Set task status to started to prevent any external monitoring from killing it
                    self._session.send_api(
                        self._session.current(tasks_api.StartedRequest)(
                            task=current_task.id,
                            status_reason="starting execution soon",
                            status_message="",
                            force=True,
                        )
                    )
            except Exception:
                if require_queue:
                    raise

        return current_task

    def _run_in_worker(self, current_task, task_id, log_level, docker, full_monitoring):
        """Run the task under this worker's monitor, in a child process or in a container."""
        if full_monitoring:
            if not (ENV_WORKER_ID.get() or "").strip():
                self._session.config["agent"]["worker_id"] = ""
            # make sure we support multiple instances if we need to
            self._singleton()
            self.temp_config_path = self.temp_config_path or safe_mkstemp(
                suffix=".cfg",
                prefix=".cairnml_agent.",
                text=True,
                name_only=True,
                dir=(ENV_TEMP_STDOUT_FILE_DIR.get() or None),
            )
            self.dump_config(filename=self.temp_config_path)
            self._session._config_file = self.temp_config_path

        worker_params = WorkerParams(
            log_level=log_level,
            config_file=self._session.config_file,
            debug=self._session.debug_mode,
            trace=self._session.trace,
            program_invocation=sys.argv[:1] if self._compiled else None,
        )
        try:
            self.report_monitor(ResourceMonitor.StatusReport(task=current_task.id))
            status = self.run_one_task(queue="", task_id=current_task.id, worker_args=worker_params, docker=docker)
        finally:
            self.stop_monitor()
            self._unregister()

            if full_monitoring and self.temp_config_path:
                safe_remove_file(self._session.config_file)
                Singleton.close_pid_file()
        return status if ENV_PROPAGATE_EXITCODE.get() else 0

    def _build_command(self, current_task, execution, script_dir, optimization, log_level):
        """Build the command line that runs the task's entry point, for a Python or a bash task."""
        is_python_binary, is_bash_binary = check_is_binary_python_or_bash(current_task.script.binary)

        extra = []
        if is_python_binary:
            extra = [
                "-u",
            ]
            if optimization:
                extra.append(WorkerParams(optimization=optimization).get_optimization_flag())
        elif is_bash_binary:
            # if we needed some arguments for bash, that's where we will add them
            extra = []

        script_full_path = None

        # check if this is a module load, then load it.
        # noinspection PyBroadException
        try:
            if is_python_binary and execution.entry_point and execution.entry_point.strip().split()[0].strip() == "-m":
                # do not parse $env when running as user
                if "$" in execution.entry_point and not ENV_TASK_EXECUTE_AS_USER.get() and is_linux_platform():
                    print("INFO: parsing environment variables: {}".format(execution.entry_point))
                    _org_env = copy(os.environ)
                    os.environ.update(self._get_job_os_envs(current_task, log_level))
                    os.environ.update(self._get_task_os_env(self._session.config, current_task) or dict())
                    parsed_cmd = shlex.split(os.path.expandvars(execution.entry_point))
                    script_full_path = Path(script_dir) / parsed_cmd[0]
                    extra.extend(parsed_cmd)
                    # restore (just in case, so we do not interfere with our local execution)
                    os.environ = _org_env
                else:
                    parsed_cmd = shlex.split(execution.entry_point)
                    script_full_path = Path(script_dir) / parsed_cmd[0]
                    extra.extend(parsed_cmd)
            elif (
                is_python_binary and execution.entry_point and execution.entry_point.strip().lower().endswith(".ipynb")
            ):
                # now we have to convert the notebook to python
                convert_extra = copy(extra)
                convert_extra.extend(["-m", "nbconvert", "--to", "python", execution.entry_point])
                convert_command = self.package_api.get_python_command(convert_extra)

                exit_code = convert_command.check_call(cwd=script_dir)
                if exit_code:
                    raise ValueError(
                        "Failed [{}] converting jupyter notebook: {}".format(exit_code, execution.entry_point)
                    )

                converted_script_filename = Path(execution.entry_point).with_suffix(".py").as_posix()
                script_full_path = Path(script_dir) / converted_script_filename

                extra.append(converted_script_filename)
            elif is_bash_binary and execution.entry_point and execution.entry_point.strip().split()[0].strip() == "-c":
                extra.append("-c")
                extra.append(" ".join(execution.entry_point.strip().split()[1:]))
            else:
                if not (Path(script_dir) / Path(execution.entry_point)).exists():
                    parsed_cmd = shlex.split(execution.entry_point)
                    script_full_path = Path(script_dir) / parsed_cmd[0]
                    extra.extend(parsed_cmd)
                    print(
                        "INFO: script entrypoint contains args, parsing and passing directly to execution: {}".format(
                            extra
                        )
                    )
                else:
                    script_full_path = Path(script_dir) / execution.entry_point
                    extra.append(execution.entry_point)

        except Exception:
            script_full_path = Path(script_dir) / execution.entry_point
            extra.append(execution.entry_point)

        # check if we need to patch entry point script
        if ENV_AGENT_FORCE_TASK_INIT.get() and script_full_path:
            patch_add_task_init_call(Path(script_full_path).as_posix())

        if is_python_binary:
            command = self.package_api.get_python_command(extra)
        elif is_bash_binary:
            # TODO: check that the venv we created for the Task is available in the bash when running `python``
            bash_binary = os.environ.get("SHELL", "/bin/bash" if os.path.exists("/bin/bash") else "/bin/sh")
            command = Argv(Path(bash_binary), *extra)
        else:
            # actually we should not be here because we default to python is we do not recognize the binary
            raise ValueError("Task execution binary requested {} is not supported!".format(current_task.script.binary))
        return command

    def _prepare_environment(self, current_task, execution, standalone_mode):
        """Create or reuse the task's Python environment, install its requirements and take the freeze.

        Returns the freeze, the repository information, the script folder and the environment folder.
        """
        # if we have no repository and binary not python and no requirements.txt
        # then skip python env creation
        no_requirements = bool(not current_task.script.requirements or not current_task.script.requirements.get("pip"))
        if (
            not execution.repository
            and no_requirements
            and (current_task.script.binary and "python" not in str(current_task.script.binary))
        ):
            ENV_AGENT_SKIP_PYTHON_ENV_INSTALL.set(1)

        if ENV_AGENT_FORCE_EXEC_SCRIPT.get():
            entry_point_parts = str(ENV_AGENT_FORCE_EXEC_SCRIPT.get()).split(":", 1)
            execution.entry_point = entry_point_parts[-1]
            execution.working_dir = entry_point_parts[0] if len(entry_point_parts) > 1 else "."
            print("WARNING: Using forced script entry [{}:{}]".format(execution.working_dir, execution.entry_point))

        python_ver = self._get_task_python_version(current_task)

        freeze = None
        repo_info = None
        script_dir = ""
        venv_folder = ""

        custom_build_script = self._session.config.get("agent.custom_build_script", "") or ENV_CUSTOM_BUILD_SCRIPT.get()
        custom_build_script, venv_folder, repo_info, script_dir = self._run_custom_build(
            current_task, execution, python_ver, custom_build_script
        )

        requested_python_version = python_ver
        if not custom_build_script:
            requirements = self._select_requirements(current_task)

            alternative_code_folder = None
            if ENV_AGENT_SKIP_PYTHON_ENV_INSTALL.get():
                venv_folder, requirements_manager, is_cached = None, None, False
                if ENV_AGENT_SKIP_PIP_VENV_INSTALL.get():
                    venv_folder, _, _, _ = self.install_virtualenv(
                        standalone_mode=standalone_mode,
                        requested_python_version=python_ver,
                        execution_info=execution,
                        cached_requirements=requirements,
                    )

                # we need to create a folder for the code to be dumped into
                code_folder = self._session.config.get("agent.venvs_dir")
                code_folder = Path(os.path.expanduser(os.path.expandvars(code_folder)))
                # let's make sure it is clear from previous runs
                if not standalone_mode:
                    rm_tree(normalize_path(code_folder, WORKING_REPOSITORY_DIR))
                    rm_tree(normalize_path(code_folder, WORKING_STANDALONE_DIR))
                if not code_folder.exists():
                    code_folder.mkdir(parents=True, exist_ok=True)
                alternative_code_folder = code_folder.as_posix()
            else:
                venv_folder, requirements_manager, is_cached, requested_python_version = self.install_virtualenv(
                    standalone_mode=standalone_mode,
                    requested_python_version=python_ver,
                    execution_info=execution,
                    cached_requirements=requirements,
                )

                if not is_cached and not standalone_mode:
                    if self._default_pip:
                        self.package_api.install_packages(*self._default_pip)

                    print("\n")

            # if we force code directory - by definition, we do not clone or apply any changes
            if ENV_AGENT_FORCE_CODE_DIR.get():
                directory, vcs, repo_info = ENV_AGENT_FORCE_CODE_DIR.get(), None, None
            else:
                # either use the venvs base folder for code or the cwd
                directory, vcs, repo_info = self.get_repo_info(
                    execution, current_task, str(alternative_code_folder or venv_folder)
                )

            print("\n")

            cwd = vcs.location if vcs and vcs.location else directory

            if not standalone_mode:
                if is_cached:
                    # reinstalling git / local packages
                    package_api = copy(self.package_api)
                    OnlyExternalRequirements.cwd = package_api.cwd = cwd
                    package_api.requirements_manager = self._get_requirements_manager(
                        base_interpreter=package_api.requirements_manager.get_interpreter(),
                        requirement_substitutions=[
                            LocalSdkRequirement,
                            CachedPackageRequirement,
                            OnlyExternalRequirements,
                        ],
                    )
                    # manually update the current state,
                    # for the external git reference chance (in the replaced callback)
                    package_api.requirements_manager.update_installed_packages_state(package_api.freeze())
                    # make sure we run the handlers
                    cached_requirements = {
                        k: package_api.requirements_manager.replace(
                            requirements=requirements[k] or "",
                            existing_packages=package_api.requirements_manager.get_installed_packages_state(),
                        )
                        for k in requirements
                    }
                    if (
                        str(cached_requirements.get("pip", "")).strip()
                        or str(cached_requirements.get("conda", "")).strip()
                    ):
                        package_api.load_requirements(cached_requirements)
                    # make sure we call the correct freeze
                    requirements_manager = package_api.requirements_manager
                elif requirements_manager:
                    self.install_requirements(
                        execution,
                        repo_info,
                        requirements_manager=requirements_manager,
                        cached_requirements=requirements,
                        cwd=cwd,
                        requested_python_version=requested_python_version,
                    )
                elif not self.package_api:
                    # check if we have to manually configure package API, it will be readonly
                    self.package_api = SystemPip(session=self._session)
            else:
                # default for standalone to get commands, implies just python
                self.package_api = SystemPip(session=self._session)

            freeze = self._take_freeze(current_task, execution, requirements_manager, venv_folder, no_requirements)

            script_dir = (
                (directory if isinstance(directory, Path) else Path(directory)).expanduser().absolute().as_posix()
            )
        return freeze, repo_info, script_dir, venv_folder

    def _run_command(self, current_task, command, script_dir, use_execv, disable_monitoring, log_file, stop_signal):
        """Run the task command: exec it when monitoring is off, otherwise run it and store its output."""
        exit_code = -1
        try:
            if disable_monitoring:
                try:
                    sys.stdout.flush()
                    sys.stderr.flush()
                    os.chdir(script_dir)
                    if use_execv:
                        os.execv(command.argv[0].as_posix(), tuple([command.argv[0].as_posix()]) + command.argv[1:])
                    else:
                        exit_code = command.check_call(cwd=script_dir)
                        # if we have an on_abort callback still running, wait for it
                        if stop_signal:
                            stop_signal.stop_monitor_thread()
                        sys.exit(exit_code)
                except subprocess.CalledProcessError as ex:
                    # non zero return code
                    exit_code = ex.returncode
                    if not use_execv:
                        sys.exit(exit_code)
                except Exception as ex:
                    if not use_execv:
                        sys.exit(-1)
                    raise ex
            else:
                # store stdout/stderr into file, and send to backend
                temp_stdout_fname = log_file or safe_mkstemp(
                    suffix=".txt",
                    prefix=".cairnml_agent_out.",
                    name_only=True,
                    dir=(ENV_TEMP_STDOUT_FILE_DIR.get() or None),
                )
                print("Storing stdout and stderr log into [%s]" % temp_stdout_fname)
                exit_code, _ = self._log_command_output(
                    task_id=current_task.id,
                    cmd=command,
                    stdout_path=temp_stdout_fname,
                    cwd=script_dir,
                )
        except KeyboardInterrupt:
            self.handle_user_abort(current_task.id)
            raise
        except Exception as e:
            self.log.warning(str(e))
            self.log_traceback(e)
            exit_code = -1
        return exit_code

    def _finish_run(self, current_task, exit_code, disable_monitoring, stop_signal):
        """Stop the helpers of the run, report the task's termination, and return the exit code."""
        # if we have an on_abort callback still running, wait for it
        if stop_signal:
            stop_signal.stop_monitor_thread()

        # kill leftover processes
        kill_all_child_processes()

        # if we return ExitStatus.interrupted==2,
        # it means user aborted, KeyboardInterrupt should have caught it,
        # that cannot happen when running with disable monitoring
        exit_code = exit_code if exit_code != ExitStatus.interrupted else -1

        if not disable_monitoring:
            # we need to change task status according to exit code
            self.handle_task_termination(current_task.id, exit_code, TaskStopReason.no_stop)
            self.stop_monitor()
            # unregister the worker
            self._unregister()

        return 1 if exit_code is None else exit_code

    def _run_custom_build(self, current_task, execution, python_ver, custom_build_script):
        """Run the custom build script, if there is one, and return what it leaves behind.

        Returns the custom build script (None when it was skipped), the environment folder, the repository
        information and the script folder.
        """
        venv_folder = ""
        repo_info = None
        script_dir = ""
        if custom_build_script:
            try:
                venv_folder = Path(self._session.config["agent.venvs_dir"], python_ver or "3")
                venv_folder = Path(os.path.expanduser(os.path.expandvars(venv_folder.as_posix())))
                directory, vcs, repo_info = self.get_repo_info(execution, current_task, str(venv_folder))
                binary, entry_point, working_dir = self.run_custom_build_script(
                    custom_build_script,
                    current_task,
                    execution,
                    venv_folder=venv_folder,
                    git_root=vcs.location,
                )

                execution.entry_point = str(entry_point)
                execution.working_dir = str(working_dir)
                script_dir = str(working_dir)

                self.package_api = VirtualenvPip(
                    session=self._session,
                    interpreter=str(binary),
                    python=str(binary),
                    requirements_manager=RequirementsManager(self._session),
                    execution_info=execution,
                    path=venv_folder,
                )

                self.global_package_api = SystemPip(
                    session=self._session,
                    interpreter=str(binary),
                )

            except SkippedCustomBuildScript as ex:
                print("Warning: {}".format(str(ex)))
                custom_build_script = None
        return custom_build_script, venv_folder, repo_info, script_dir

    def _select_requirements(self, current_task):
        """Return the requirements the task asks for, or the repository's or the original ones when the configuration says so."""
        if self._session.config.get("agent.package_manager.force_repo_requirements_txt", False):
            requirements = None
            print(
                "\n[package_manager.force_repo_requirements_txt=true] "
                'Skipping requirements, using repository "requirements.txt" \n'
            )
        elif self._session.config.get("agent.package_manager.force_original_requirements", False):
            try:
                requirements = current_task.script.requirements
                if isinstance(requirements, dict):
                    # prefer "pip" we do not fill in "conda" entry in the SDK
                    if "org_pip" in requirements:
                        requirements["pip"] = requirements["org_pip"]
                        print(
                            "\n[package_manager.force_original_requirements=true] "
                            "Using original requirements: \n{}\n".format(requirements["org_pip"])
                        )
                    elif "org_conda" in requirements:
                        requirements["conda"] = requirements["org_conda"]
                        print(
                            "\n[package_manager.force_original_requirements=true] "
                            "Using original requirements: \n{}\n".format(requirements["org_conda"])
                        )
            except AttributeError:
                requirements = None
        else:
            try:
                requirements = current_task.script.requirements
            except AttributeError:
                requirements = None
        return requirements

    def _take_freeze(self, current_task, execution, requirements_manager, venv_folder, no_requirements):
        """Take the freeze of the task environment and update the task's requirements, unless that is skipped."""
        freeze = None
        # do not update the task packages if we are using conda,
        # it will most likely make the task environment unreproducible
        skip_freeze_update = self.is_conda and not self._session.config.get(
            "agent.package_manager.conda_full_env_update", False
        )

        # skip update requirements on nodes that are not Rank 0 (only update requirements on RANK 0)
        if self._get_node_rank():
            skip_freeze_update = True

        # skip python freeze if we installed nothing
        # (i.e. no requirements and no git repo to install requirement from there)
        skip_freeze = ENV_AGENT_SKIP_PYTHON_ENV_INSTALL.get() or (no_requirements and not execution.repository)

        if not skip_freeze:
            freeze = self.freeze_task_environment(
                task_id=current_task.id,
                requirements_manager=requirements_manager,
                add_venv_folder_cache=venv_folder,
                execution_info=execution,
                update_requirements=not skip_freeze_update,
            )
        return freeze

    def _get_job_os_envs(self, current_task, log_level):
        sdk_env = {
            # config_file updated in session.py
            "task_id": current_task.id,
            "log_level": log_level,
            "log_to_backend": "0",
            "config_file": self._session.config_file,  # The config file is the tmp file that cairnml_agent created
        }
        envs = {sdk_key: str(value) for key, value in sdk_env.items() for sdk_key in ENVIRONMENT_SDK_PARAMS[key]}
        return envs

    def _get_task_os_env(self, config, current_task):
        if not config.get("agent.enable_task_env", None):
            return None
        if not self._session.serves("tasks", "get_hyper_params"):
            return None
        # noinspection PyBroadException
        try:
            hyper_params = self._session.send_api(
                self._session.current(tasks_api.GetHyperParamsRequest)(tasks=[current_task.id])
            ).to_dict()
            hyper_params = {
                str(p["name"]): str(p["value"])
                for p in hyper_params["params"][0]["hyperparams"]
                if p["section"] == "Environment"
            }

            return hyper_params
        except Exception:
            return None

    def set_docker_variables(self, docker):
        temp_config, docker_image_func = self.get_docker_config_cmd(docker)
        self.dump_config(self.temp_config_path, config=temp_config)
        self.docker_image_func = docker_image_func

    def get_execution_info(self, current_task):
        # type: (...) -> ExecutionInfo
        try:
            execution = ExecutionInfo.from_task(current_task)
        except Exception as e:
            self.error("Could not parse task execution info: {}".format(e.args[0]))
            current_task.failed(status_reason=e.args[0], status_message=self._task_status_change_message)
            self.exit(e.args[0])

        if "\\" in execution.working_dir:
            warning(
                'Working dir "{}" contains backslashes. All path separators must be forward slashes.'.format(
                    execution.working_dir
                )
            )

        print("Executing task id [%s]:" % current_task.id)
        sanitized_execution = attr.evolve(
            execution,
            docker_cmd=" ".join(
                DockerArgsSanitizer.sanitize_docker_command(self._session, shlex.split(execution.docker_cmd or ""))
            ),
        )
        for pair in attr.asdict(sanitized_execution).items():
            print("{} = {}".format(*pair))
        print()
        return execution

    def get_repo_info(self, execution, task, venv_folder):
        # type: (ExecutionInfo, tasks_api.Task, str) -> Tuple[str, Optional[VCS], Optional[RepoInfo]]
        literal_script = LiteralScriptManager(venv_folder)
        has_repository = bool(execution.repository)
        is_literal_script = literal_script.is_literal_script(task)
        if not has_repository and not is_literal_script:
            print("WARNING: running a task without repository or literal script in `script.diff`")
            location = Path(venv_folder) / WORKING_STANDALONE_DIR
            location.mkdir(exist_ok=True, parents=True)
            return location.as_posix(), None, None

        repo_info = None
        directory = None
        vcs = None
        script_file = None
        if has_repository:
            vcs, repo_info = self._get_repo_info(execution, task, venv_folder)
            directory = Path(repo_info.root, execution.working_dir or ".")

            for pair in attr.asdict(repo_info).items():
                print("{}: {}".format(*pair))
            if not is_literal_script:
                self.apply_diff(task=task, vcs=vcs, execution_info=execution, repo_info=repo_info)
            script_file = Path(directory) / execution.entry_point
            if not script_file.is_file():
                # try to parse with shlex
                try:
                    script_file = Path(directory) / shlex.split(execution.entry_point)[0]
                except Exception:
                    pass

        if is_literal_script:
            self.log.info("found literal script in `script.diff`")
            directory, entry_point, script = literal_script.create_notebook_file(task, execution, repo_info)
            execution.entry_point = entry_point
            script_file = Path(script)
        else:
            # in case of no literal script, there is not difference between empty working dir and `.`
            execution.working_dir = execution.working_dir or "."

        # fix our import patch (in case we have __future__)
        try:
            is_file = script_file and script_file.is_file()
        except OSError:
            is_file = False
        if is_file:
            fix_package_import_diff_patch(script_file.as_posix())

        if is_literal_script and not has_repository:
            return directory, None, None

        if not directory:
            assert False, "unreachable code"
        return directory, vcs, repo_info

    def _get_repo_info(self, execution, task, venv_folder):
        try:
            self._session.config.put("agent.standalone_mode", self._standalone_mode)
            vcs, repo_info = clone_repository_cached(
                session=self._session,
                execution=execution,
                destination=Path(venv_folder) / WORKING_REPOSITORY_DIR,
                task_id=task.id,
            )
        except CommandFailedError:
            raise
        except Exception as ex:
            print("Repository cloning failed: {}".format(ex))
            task.failed(
                status_reason="failed cloning repository",
                status_message=self._task_status_change_message,
            )
            if self._session.debug_mode:
                raise
            raise CommandFailedError(
                "Failed cloning repository. \n"
                "1) Make sure you pushed the requested commit:\n{}\n"
                "2) Check if remote-worker has valid credentials [see worker configuration file]".format(
                    str(execution).replace("ExecutionInfo", "", 1).replace("version_num", "commit_id", 1)
                )
            ) from ex
        return vcs, repo_info

    def handle_task_termination(self, task_id, exit_code, stop_reason, session=None):
        # type: (Text, int, TaskStopReason) -> None
        session = session or self._session
        try:
            if stop_reason == TaskStopReason.stopped:
                # do not change the status to stopped if the Task status is already failed
                task_status = get_task(session, task_id, only_fields=["status"]).status
                if str(task_status) == "failed":
                    self.send_logs(task_id, ["Process aborted by user - Task status was Failed"], session=session)
                    self.log("Stopping - task was already marked as failed")
                else:
                    self.send_logs(task_id, ["Process aborted by user"], session=session)
                    self.log("Stopping - tasks.stop was called for task")
                    session.send_api(
                        tasks_api.StoppedRequest(
                            task=task_id,
                            status_reason="task was stopped by tasks.stop",
                            status_message=self._task_status_change_message,
                            force=False,
                        )
                    )

            elif stop_reason == TaskStopReason.status_changed:
                try:
                    task_status = get_task(session, task_id, only_fields=["status"]).status
                    self.log(
                        "Task status changed unexpectedly (status: {}), terminating task process.".format(task_status)
                    )
                except Exception as ex:
                    self.log(
                        "Task status changed unexpectedly. Was not able to obtain the current status: {}: {}".format(
                            type(ex), ex
                        )
                    )
                    self.log_traceback(ex)

            elif stop_reason == TaskStopReason.reset:
                self.log("Task was reset unexpectedly")

            elif stop_reason == TaskStopReason.no_stop:
                self.handle_task_process_termination(task_id, exit_code, session=session)

            elif stop_reason == TaskStopReason.not_found:
                self.log("Task not found")

            else:
                self.log("INTERNAL ERROR: unidentified task stop reason: {}".format(stop_reason))

        except Exception as e:
            # task probably set its own status
            self.log("Warning: could not update task id '{}' status. Task exit code {}".format(task_id, exit_code))
            self.log_traceback(e)

    @staticmethod
    def _get_node_rank():
        # type: () -> int
        # noinspection PyBroadException
        try:
            rank = int(os.environ.get("RANK", os.environ.get("SLURM_PROCID")) or 0)
        except Exception:
            rank = 0
        return rank

    def handle_task_process_termination(self, task_id, exit_code, session=None):
        # type: (Text, int) -> None
        session = session or self._session
        rank = self._get_node_rank()
        rank_text = " - rank {}".format(rank) if rank else ""

        self.log("Task process terminated" + rank_text)
        # only RANK 0 can change the Task status.

        if exit_code == COMMAND_SUCCESS:
            self.log("Task success: completing" + rank_text)
            self.send_logs(task_id, ["Process completed successfully" + rank_text], session=session)
            if not rank:
                session.send_api(
                    tasks_api.CompletedRequest(
                        task=task_id,
                        status_reason="worker execution done",
                        status_message=self._task_status_change_message,
                    )
                )
        elif exit_code in (ExitStatus.interrupted, 256 + ExitStatus.interrupted):
            self.log("Task interrupted: stopping" + rank_text)
            self.send_logs(task_id, ["Process terminated by user" + rank_text], session=session)
            if not rank:
                session.send_api(
                    tasks_api.StoppedRequest(
                        task=task_id,
                        status_reason="user abort",
                        status_message=self._task_status_change_message,
                    )
                )
        else:
            self.log("Task failure: setting status to 'failed'" + rank_text)
            self.send_logs(task_id, ["Process failed, exit code {}".format(exit_code) + rank_text], session=session)
            if not rank:
                session.send_api(
                    tasks_api.FailedRequest(
                        task=task_id,
                        status_reason="worker execution exit code {}".format(exit_code),
                        status_message=self._task_status_change_message,
                    )
                )

    def freeze_task_environment(
        self,
        task_id=None,
        requirements_manager=None,
        add_venv_folder_cache=None,
        execution_info=None,
        update_requirements=False,
    ):
        try:
            freeze = self.package_api.freeze()
        except Exception as e:
            print("Could not freeze installed packages")
            self.log_traceback(e)
            return None

        if requirements_manager:
            freeze = requirements_manager.replace_back(freeze)

        if not task_id:
            return freeze

        # get original requirements and update with the new frozen requirements
        previous_reqs = {}
        # noinspection PyBroadException
        try:
            current_task = get_task(self._session, task_id, only_fields=["script.requirements"])
            requirements = current_task.script.requirements
            previous_reqs = dict(**requirements)
            # replace only once.
            if requirements.get("pip") and not requirements.get("org_pip"):
                requirements["org_pip"] = requirements.pop("pip")
            if requirements.get("conda") and not requirements.get("org_conda"):
                requirements["org_conda"] = requirements.pop("conda")
            requirements.update(freeze)
        except Exception:
            requirements = freeze

        # disable caching with poetry because we cannot make it install into a specific folder
        # Todo: add support for poetry caching
        if not self.poetry.enabled and not self.uv.enabled:
            # disable caching if we skipped the venv creation or the entire python setup
            if (
                add_venv_folder_cache
                and not self._standalone_mode
                and (not ENV_AGENT_SKIP_PIP_VENV_INSTALL.get() and not ENV_AGENT_SKIP_PYTHON_ENV_INSTALL.get())
            ):
                # add to cache
                self.package_api.add_cached_venv(
                    requirements=[freeze, previous_reqs],
                    docker_cmd=execution_info.docker_cmd if execution_info else None,
                    python_version=getattr(self.package_api, "python", ""),
                    cuda_version=self._session.config.get("agent.cuda_version"),
                    source_folder=add_venv_folder_cache,
                    exclude_sub_folders=[WORKING_REPOSITORY_DIR, WORKING_STANDALONE_DIR],
                )

        # If do not update back requirements
        if not update_requirements:
            return freeze

        request = tasks_api.SetRequirementsRequest(task=task_id, requirements=requirements)
        try:
            self._session.send_api(request)
        except APIError as e:
            print("Could not set task requirements")
            self.log_traceback(e)
        return freeze

    def _install_poetry_requirements(self, repo_info, working_dir=None):
        # type: (Optional[RepoInfo], Optional[str]) -> Optional[PoetryAPI]
        if not repo_info:
            return None

        files_from_working_dir = self._session.config.get(
            "agent.package_manager.poetry_files_from_repo_working_dir", False
        )
        lockfile_path = Path(repo_info.root) / ((working_dir or "") if files_from_working_dir else "")

        try:
            if not self.poetry.enabled:
                return None

            self.poetry.initialize(cwd=lockfile_path)
            api = self.poetry.get_api(lockfile_path)
            if api.enabled:
                print("Poetry Enabled: Ignoring requested python packages, using repository poetry lock file!")
                api.install()
                return api

            print(f"Could not find pyproject.toml or poetry.lock file in {lockfile_path} \n")
        except Exception as ex:
            self.log.error("failed installing poetry requirements: {}".format(ex))
        return None

    def _install_uv_requirements(self, repo_info, working_dir=None, cached_requirements=None):
        # type: (Optional[RepoInfo], Optional[str], Optional[Dict[str, list]]) -> Optional[UvAPI]
        if not repo_info:
            return None

        if not isinstance(self.package_api, UvAPI):
            return None

        files_from_working_dir = self._session.config.get("agent.package_manager.uv_files_from_repo_working_dir", False)
        lockfile_path = Path(repo_info.root) / ((working_dir or "") if files_from_working_dir else "")

        self.package_api.set_lockfile_path(lockfile_path)

        if self.package_api.enabled:
            print("UV Enabled: Ignoring requested python packages, using repository uv lock file!")
            # noinspection PyBroadException
            try:
                self.package_api.install()
                return self.package_api
            except Exception as ex:
                self.log.error("Failed installing uv requirements from lock file: {}".format(ex))
                # if we have a lock file we actually fail, otherwise we revert to pip behaviour
                if self.package_api.lock_file_exists:
                    raise
                self.log.error(
                    "Reverting to UV as pip replacement - installing from Task requirements or git requirements.txt"
                )
                self.package_api._enabled = False
                return None

        # we will create the "regular" venv style using UV
        return None

    def install_requirements(
        self,
        execution,
        repo_info,
        requirements_manager,
        cached_requirements=None,
        cwd=None,
        package_api=None,
        requested_python_version=None,
    ):
        ExternalRequirements.cwd = cwd
        return self.install_requirements_for_package_api(
            execution,
            repo_info,
            requirements_manager,
            cached_requirements=cached_requirements,
            cwd=cwd,
            package_api=package_api if package_api else self.package_api,
            requested_python_version=requested_python_version,
        )

    def _install_patched_python_requirements(self, execution, package_api, cached_requirements):
        self.log("Found task requirements section, trying to install")
        cached_requirements = cached_requirements or {}

        if ENV_AGENT_FORCE_TASK_INIT.get():
            # notice we have PackageCollectorRequirement to protect against double includes of "cairnml"
            print("Force cairnml Task.init patch adding cairnml package to requirements")
            if not cached_requirements or cached_requirements.get("pip"):
                cached_requirements["pip"] = (
                    ("cairnml\n" + cached_requirements.get("pip", ""))
                    if isinstance(cached_requirements.get("pip", ""), str)
                    else (["cairnml"] + cached_requirements.get("pip", []))
                )
            if cached_requirements.get("conda"):
                cached_requirements["conda"] = (
                    ("cairnml\n" + cached_requirements["conda"])
                    if isinstance(cached_requirements["conda"], str)
                    else (["cairnml"] + cached_requirements["conda"])
                )

        # check if we need to push jupyter nbconvert if we need to run ipynb
        if execution.entry_point and execution.entry_point.strip().lower().endswith(".ipynb"):
            print("Force nbconvert patch to convert .ipynb to python")
            # now we have to make sure we run it with jupyter notebook
            if not cached_requirements or cached_requirements.get("pip"):
                cached_requirements["pip"] = (
                    ("nbconvert\nipython\n" + cached_requirements.get("pip", ""))
                    if isinstance(cached_requirements.get("pip", ""), str)
                    else (["nbconvert", "ipython"] + cached_requirements.get("pip", []))
                )
            if cached_requirements.get("conda"):
                cached_requirements["conda"] = (
                    ("nbconvert\nipython\n" + cached_requirements["conda"])
                    if isinstance(cached_requirements["conda"], str)
                    else (["nbconvert", "ipython"] + cached_requirements["conda"])
                )

        if cached_requirements and (cached_requirements.get("conda") or cached_requirements.get("pip")):
            try:
                package_api.load_requirements(cached_requirements)
            except Exception as e:
                self.log_traceback(e)
                raise ValueError("Could not install task requirements!\n{}".format(e)) from e
            else:
                self.log("task requirements installation passed")
                return True

        return False

    def install_requirements_for_package_api(
        self,
        execution,
        repo_info,
        requirements_manager,
        cached_requirements=None,
        cwd=None,
        package_api=None,
        requested_python_version=None,
    ):
        # type: (ExecutionInfo, RepoInfo, RequirementsManager, Optional[dict], Optional[str], Optional[Any], Optional[str]) -> None
        """
        :summary: Install requirements for task script using pip.
        :description: A file named "requirements.txt" is looked for in each containing folder between the
                      repository root and the directory containing the script.
                      For each such file, CUDA based packages are looked for and replaced if necessary.
        :param execution: task execution information
        :param repo_info: repository information
        :param requirements_manager: requirements manager for task
        :param cached_requirements: cached requirements from previous run
        :param cwd: current folder
        :param package_api: package_api to be used when installing requirements
        :param requested_python_version: requested python version, string format, only uses major/minor version
        """
        if package_api:
            package_api.cwd = cwd

        # convert to tuple
        # noinspection PyBroadException
        try:
            requested_python_version = str(requested_python_version or "3").split(".")
            requested_python_version = (int(requested_python_version[0]), int(requested_python_version[1]))
        except Exception:
            requested_python_version = (3, -1)

        api = self._install_poetry_requirements(repo_info, execution.working_dir)
        is_uv = False
        is_poetry = bool(api)
        if not api:
            api = self._install_uv_requirements(repo_info, execution.working_dir, cached_requirements)
            # if we could not find a lock file, but UV is installed and enabled, use it instead of pip
            if not api and not self._session.config.get("agent.package_manager.uv_replace_pip", False):
                if package_api == self.package_api and isinstance(self.package_api, UvAPI):
                    # revert to venv that we used inside UV
                    api = None
                    self.package_api = package_api = package_api.get_venv_manager()
            elif self._session.config.get("agent.package_manager.type", None) == "uv" and not api:
                # this means `agent.package_manager.uv_replace_pip` is set to true
                print("INFO: using UV as pip drop-in replacement")
                is_uv = True

        if api:
            # update back the package manager, this hack should be fixed
            if package_api == self.package_api:
                self.package_api = api
            elif package_api == self.global_package_api:
                self.global_package_api = api

            if ENV_AGENT_FORCE_TASK_INIT.get():
                # noinspection PyBroadException
                try:
                    (package_api or self.package_api or self.global_package_api).load_requirements({"pip": "cairnml"})
                except Exception:
                    print("Warning: failed to install CairnML package")

            return

        package_api.upgrade_pip()
        package_api.set_selected_package_manager()
        # always install cython, BUT only iif python version is lower than 3.9 (new versions can take care of themselves)
        # if we have a specific version in the requirements,
        # the PriorityPackageRequirement(SimpleSubstitution) will reinstall cython with the specific version
        if requested_python_version[1] < 9:
            if not self.is_conda and not is_uv and not is_poetry:
                package_api.out_of_scope_install_package("Cython")

        # add support for -r <file.txt> in requirements
        if requirements_manager:
            requirements_manager.set_cwd(cwd)

        # use task requirements when explicitly provided
        # if the section exists but is empty, we install the requirements.txt files
        if cached_requirements and (
            cached_requirements.get("pip") is not None or cached_requirements.get("conda") is not None
        ):
            if self._install_patched_python_requirements(execution, package_api, cached_requirements):
                return

        if not repo_info:
            # if we had cached_requirements, it will install them (or raise exception if failed)
            # if we had nothing to install at least output a warning.
            if not self._install_patched_python_requirements(execution, package_api, cached_requirements):
                self.log("no repository to install requirements from")
            return

        repo_requirements_installed = False
        patched_requirements = False
        for parent in reversed((Path(execution.working_dir) / execution.entry_point).parents):
            requirements_file = repo_info.root / parent / "requirements.txt"  # type: Path
            if not requirements_file.is_file():
                continue
            print("Trying pip install: {}".format(requirements_file))
            requirements_text = requirements_file.read_text()
            if ENV_AGENT_FORCE_TASK_INIT.get() and not patched_requirements:
                # notice we have PackageCollectorRequirement to protect against double includes of "cairnml"
                print("Force cairnml Task.init patch adding cairnml package to requirements")
                requirements_text = "cairnml\n" + requirements_text

            if (
                execution.entry_point
                and execution.entry_point.strip().lower().endswith(".ipynb")
                and not patched_requirements
            ):
                # notice we have PackageCollectorRequirement to protect against double includes of "cairnml"
                print("Force nbconvert patch to convert .ipynb to python")
                requirements_text = "nbconvert\nipython\n" + requirements_text

            new_requirements = requirements_manager.replace(requirements_text)
            patched_requirements = True
            temp_file = None
            try:
                with self.named_temporary_file(
                    mode="w", prefix="requirements_", suffix=".txt", delete=False
                ) as temp_file:
                    temp_file.write(new_requirements)
                    temp_file.flush()
                # close the file before reading in install_from_file for Windows compatibility
                package_api.install_from_file(temp_file.name)
            except Exception as e:
                print("ERROR: Failed installing requirements.txt:\n{}".format(requirements_text))
                raise e
            finally:
                if self._session.debug_mode and temp_file:
                    rm_file(temp_file.name)
            # call post installation callback
            requirements_manager.post_install(self._session, package_manager=package_api)
            # mark as successful installation
            repo_requirements_installed = True

        # if we still have not installed anything, see if we need to install something becuase we patched
        if not repo_requirements_installed:
            repo_requirements_installed = self._install_patched_python_requirements(
                execution, package_api, cached_requirements
            )

        # if we reached here without installing anything, and
        # we failed installing from cached requirements, them this is an error
        if not repo_requirements_installed:
            print("Warning: installing NO task requirements or repository requirements")

    def named_temporary_file(self, *args, **kwargs):
        kwargs.setdefault("delete", not self._session.debug_mode)
        return named_temporary_file(*args, **kwargs)

    def list(self, **_):
        # display all registered working machines
        workers = self._session.send_api(self._session.current(workers_api.GetAllRequest)(last_seen=0)).to_dict()
        print_parameters(workers)

    def _register(self, queues=()):
        self.queues = queues
        try:
            self._session.send_api(
                self._session.current(workers_api.RegisterRequest)(worker=self.worker_id, queues=list(queues))
            )
            # If we got here - we've registered
            self._worker_registered = True
        # except APIError as error:
        #     if error.codes != WORKER_ALREADY_REGISTERED:
        #         raise
        # except ValueError:
        #     raise ValueError("Worker cannot register itself with backend service")
        except Exception as e:
            self.log("Worker failed registering itself with backend service: {}".format(e))

    def _unregister(self, queues=()):
        self.queues = ()
        try:
            self._session.send_api(self._session.current(workers_api.UnregisterRequest)(worker=self.worker_id))
            self._worker_registered = False
        except Exception as e:
            self.log("Worker failed unregistering itself with backend service: {}".format(e))

    def log_traceback(self, err):
        if isinstance(err, APIError):
            tb = err.get_traceback()
            if tb:
                print("Server traceback:\n{}".format(tb))
        if self._session.debug_mode:
            self.log(traceback.format_exc())

    def debug(self, message, context=None):
        if self._session.debug_mode and (not context or context == self._debug_context):
            print("cairnml_agent: {}".format(message))

    @staticmethod
    def _get_python_version_suffix(executable_path):
        # type: (Text) -> Text
        """
        Platform independent function that returns version substring from the python executable
        """

        def rreplace(s, old, new, count):
            return (s[::-1].replace(old[::-1], new[::-1], count))[::-1]

        if is_windows_platform():
            return rreplace(rreplace(executable_path.split(os.path.sep)[-1].lower(), "python", "", 1), ".exe", "", 1)

        return rreplace(executable_path.split(os.path.sep)[-1], "python", "", 1)

    def find_python_executable_for_version(self, config_version, raise_on_error=True):
        # type: (Text, bool) -> Tuple[Text, Text, Text]
        """
        Find python executable with version ``config_version``.
        The search starts with the executable named ``python<config_version>``.
        If not found in the path, increasingly major components of the semantic version are dropped
        with the last executable to be searched being a plain ``python``.
        A result is returned only if ``python --version``'s output matches config_version.
        For example: if config_version=3.6.5, the search order is:
        1. python3.6.5
        2. python3.6
        3. python3
        4. python
        if config_version=3.6, the search starts with ``python3.6`` and so on.
        :return: 3-tuple:
            1. Python executable version as reported by itself with ``--version``
            2. The version suffix of the executable name (e.g. ``python3`` -> ``3``)
            3. The executable name itself (e.g. ``python3``)
        """

        def suffixes(it):
            it = list(it)
            for i in range(len(it) + 1):
                yield it[:i]

        python_executables = [
            (version, config_version if os.path.sep in config_version else "python{}".format(version))
            for version in map(
                ".".join, reversed(list(suffixes(self._get_python_version_suffix(config_version).split("."))))
            )
        ]

        # if we actually have a local python
        if not self._compiled:
            python_executables += [("{}.{}".format(sys.version_info.major, sys.version_info.minor), sys.executable)]

        default_python = None
        for version, executable in python_executables:
            self.log.debug("Searching for {}".format(executable))
            found_exec = find_executable(executable)
            self.log.debug("Found executable: {}".format(found_exec))
            if found_exec:
                try:
                    output = Argv(found_exec, "--version").get_output(stderr=subprocess.STDOUT)
                except subprocess.CalledProcessError as ex:
                    # Windows returns 9009 code and suggests to install Python from Windows Store
                    if is_windows_platform() and ex.returncode == 9009:
                        self.log.debug("version not found: {}".format(ex))
                    elif raise_on_error:
                        self.log.warning("error getting {} version: {}".format(executable, ex))
                    else:
                        self.log.debug("error getting {} version: {}".format(executable, ex))
                    continue
                except FileNotFoundError as ex:
                    self.log.debug("version not found: {}".format(ex))
                    continue

                if not default_python:
                    match = re.search(r"Python ({}(?:\.\d+)*)".format(r"\d+"), output)
                    default_python = (
                        match.group(1),
                        version if version and "." in version else ".".join(match.group(1).split(".")[:2]),
                        executable,
                    )

                match = re.search(
                    r"Python ({}(?:\.\d+)*)".format(
                        r"\d+" if not config_version or os.path.sep in config_version else config_version
                    ),
                    output,
                )
                if match:
                    self.log.debug("Found: {}".format(executable))
                    return (
                        match.group(1),
                        version if version and "." in version else ".".join(match.group(1).split(".")[:2]),
                        executable,
                    )

        if default_python:
            if raise_on_error:
                # only log waning is we rais on error, otherwise this is not an issue
                self.log.warning(
                    "Python executable with version {!r} requested by the Task, "
                    "not found in path, using '{}' (v{}) instead".format(
                        config_version, find_executable(default_python[-1]), default_python[0]
                    )
                )
            return default_python

        if not raise_on_error:
            return "", "", ""

        raise CommandFailedError(
            "Python executable with version {!r} requested by the Task, "
            "key 'agent.default_python', not found in path, tried: {}".format(
                config_version, list(zip(*python_executables))[1]
            )
        )

    def run_custom_build_script(self, script, task, execution, venv_folder, git_root):
        # type: (str, tasks_api.Task, ExecutionInfo, Path, str)-> Tuple[Path, Path, Path]
        """
        Run a custom env build script
        :param script:
        :return: A tuple containing:
            - a full path to a python executable
            - a new task entry_point (replacing the entry_point in the task's script section)
            - a new working directory (replacing the working_dir in the task's script section)
            - a requirements manager instance
        """
        os.environ["CAIRNML_TASK_SCRIPT_ENTRY"] = execution.entry_point
        os.environ["CAIRNML_TASK_WORKING_DIR"] = execution.working_dir
        os.environ["CAIRNML_VENV_PATH"] = str(venv_folder)
        os.environ["CAIRNML_GIT_ROOT"] = git_root

        script = os.path.expanduser(os.path.expandvars(script))

        try:
            if not os.path.isfile(script):
                raise SkippedCustomBuildScript("Build script {} is not found".format(script))
        except OSError as ex:
            raise SkippedCustomBuildScript(str(ex)) from ex

        print("Running custom build script {}".format(script))

        script_output_file = NamedTemporaryFile(prefix="custom_build_script", suffix=".json", mode="wt", delete=False)

        os.environ["CAIRNML_AGENT_CUSTOM_BUILD_SCRIPT"] = script
        os.environ["CAIRNML_CUSTOM_BUILD_TASK_CONFIG_JSON"] = json.dumps(
            task.to_dict(), separators=(",", ":"), default=str
        )
        os.environ["CAIRNML_CUSTOM_BUILD_OUTPUT"] = script_output_file.name

        try:
            subprocess.check_call([script])
        except subprocess.CalledProcessError as ex:
            raise CustomBuildScriptFailed(
                message="Custom build script failed with return code {}".format(ex.returncode), errno=ex.returncode
            ) from ex

        output = Path(script_output_file.name).read_text()
        if not output:
            raise SkippedCustomBuildScript("Build script {} did not return any output".format(script))

        try:
            output = json.loads(output)
            binary = Path(output["binary"])
            entry_point = Path(output["entry_point"])
            working_dir = Path(output["working_dir"])
        except ValueError as ex:
            raise SkippedCustomBuildScript(
                "Failed parsing build script output JSON ({}): {}".format(script_output_file.name, ex)
            ) from ex
        except KeyError as ex:
            raise SkippedCustomBuildScript("Build script output missing {} field".format(ex.args[0])) from ex

        try:
            if not binary.is_file():
                raise SkippedCustomBuildScript(
                    "Invalid binary path returned from custom build script: {}".format(binary)
                )
            if not entry_point.is_file():
                raise SkippedCustomBuildScript(
                    "Invalid entrypoint path returned from custom build script: {}".format(entry_point)
                )
            if not working_dir.is_dir():
                raise SkippedCustomBuildScript(
                    "Invalid working dir returned from custom build script: {}".format(working_dir)
                )
        except OSError as ex:
            raise SkippedCustomBuildScript(str(ex)) from ex

        return binary, entry_point, working_dir

    def _get_skip_pip_venv_install(self, skip_pip_venv_install=None):
        if skip_pip_venv_install is None:
            skip_pip_venv_install = ENV_AGENT_SKIP_PIP_VENV_INSTALL.get()

        if skip_pip_venv_install:
            try:
                skip_pip_venv_install = bool(strtobool(skip_pip_venv_install))
            except ValueError:
                pass
        elif (
            ENV_VENV_CONFIGURED.get()
            and ENV_DOCKER_IMAGE.get()
            and self._session.config.get("agent.docker_use_activated_venv", True)
            and self._session.config.get("agent.package_manager.system_site_packages", False)
        ):
            # if we are running inside a container, and virtual environment is already installed,
            # we should install directly into it, because we cannot inherit from the system packages
            skip_pip_venv_install = find_executable("python") or True

            # check if we are running inside a container:
            print(
                "Warning! Found python virtual environment [{}] already activated inside the container, "
                "installing packages into venv (pip does not support inherit/nested venv)".format(
                    skip_pip_venv_install if isinstance(skip_pip_venv_install, str) else ENV_VENV_CONFIGURED.get()
                )
            )
        return skip_pip_venv_install

    def install_virtualenv(
        self,
        venv_dir=None,
        requested_python_version=None,
        standalone_mode=False,
        execution_info=None,
        cached_requirements=None,
    ):
        # type: (str, str, bool, ExecutionInfo, dict) -> Tuple[Path, RequirementsManager, bool, str]
        """
        Install a new python virtual environment, removing the old one if exists
        If skip_pip_venv_install is True or contains a string (or if CAIRNML_AGENT_SKIP_PIP_VENV_INSTALL is set)
        then an emtpy virtual env folder is created and package manager is configured to work with the global python
        interpreter (or using a custom interpreter if an interpreter path is passed in this variable)
        :return: virtualenv directory, requirements manager to use with task, True if there is a cached venv entry
        """
        skip_pip_venv_install = self._get_skip_pip_venv_install()

        if self._session.config.get("agent.ignore_requested_python_version", None):
            requested_python_version = ""

        # if we have no requested version at all, and this is compiled, so no preinstalled default version
        # just assume generic python version 3 (if we found something we will use it,
        # otherwise we will revert to the compiled version python)
        if self._compiled and not requested_python_version:
            requested_python_version = "3"

        requested_python_version = (
            requested_python_version
            or Text(self._session.config.get("agent.python_binary", None))
            or Text(self._session.config.get("agent.default_python", None))
            or "{}.{}".format(sys.version_info.major, sys.version_info.minor)
        )

        if self.is_conda:
            executable_version_suffix = requested_python_version[
                max(requested_python_version.find("python"), 0) :
            ].replace("python", "")
            executable_name = "python"
        else:
            override_interpreter_path = None
            if skip_pip_venv_install and isinstance(skip_pip_venv_install, str):
                if find_executable(skip_pip_venv_install):
                    override_interpreter_path = skip_pip_venv_install
                else:
                    print(
                        "Warning: interpreter {} could not be found. "
                        "Reverting to the default interpreter resolution".format(skip_pip_venv_install)
                    )

            if override_interpreter_path:
                print("Python interpreter {} is set from environment var".format(override_interpreter_path))
                executable_name = override_interpreter_path
                executable_version_suffix = get_python_version(
                    executable_name, self.log
                ) or self._get_python_version_suffix(executable_name)
            elif not self.uv.enabled:
                try:
                    executable_version, executable_version_suffix, executable_name = (
                        self.find_python_executable_for_version(requested_python_version)
                    )
                except Exception:
                    def_python_version = (
                        Text(self._session.config.get("agent.python_binary", None))
                        or Text(self._session.config.get("agent.default_python", None))
                        or "{}.{}".format(sys.version_info.major, sys.version_info.minor)
                    )
                    print(
                        "Warning: could not locate requested Python version {}, reverting to version {}".format(
                            requested_python_version, def_python_version
                        )
                    )
                    executable_version, executable_version_suffix, executable_name = (
                        self.find_python_executable_for_version(def_python_version)
                    )
            else:
                # this is UV, we find it just so we know what we have there
                # noinspection PyBroadException
                try:
                    executable_version, executable_version_suffix, executable_name = (
                        self.find_python_executable_for_version(requested_python_version, raise_on_error=False)
                    )
                except Exception:
                    executable_version_suffix = executable_name = None
                # make sure we have at least major.minor version in the requested version,
                # otherwise use what we found
                if "." not in str(requested_python_version):
                    requested_python_version = executable_version_suffix
                # if the requested python version is Not what we found,
                # we do not want UV to use it, we want UV to install python, so we set executable_name to None
                if requested_python_version != executable_version_suffix:
                    executable_name = None
                    executable_version_suffix = requested_python_version

            self.debug(
                "debug: requested_python_version={}, executable_name={}".format(
                    executable_version_suffix, executable_name
                )
            )

            if executable_version_suffix:
                self._session.config.put("agent.default_python", executable_version_suffix)
            else:
                executable_version_suffix = requested_python_version or "3"

            if executable_name:
                self._session.config.put("agent.python_binary", executable_name)

        venv_dir = (
            Path(venv_dir) if venv_dir else Path(self._session.config["agent.venvs_dir"], executable_version_suffix)
        )
        venv_dir = Path(os.path.expanduser(os.path.expandvars(venv_dir.as_posix())))

        pip_version = get_specific_package_version(cached_requirements, package_name="pip")
        if pip_version:
            PackageManager.set_pip_version(pip_version)
        if self.uv.enabled:
            self.uv.set_python_version(requested_python_version)
            uv_version = get_specific_package_version(cached_requirements, package_name="uv")
            if uv_version:
                self.uv.set_uv_version(uv_version)

        first_time = not standalone_mode and (is_windows_platform() or self.is_conda or not venv_dir.is_dir())

        if not standalone_mode:
            rm_tree(normalize_path(venv_dir, WORKING_REPOSITORY_DIR))
            rm_tree(normalize_path(venv_dir, WORKING_STANDALONE_DIR))

        call_package_manager_create, requirements_manager = self._setup_package_api(
            executable_name=executable_name,
            executable_version_suffix=executable_version_suffix,
            venv_dir=venv_dir,
            execution_info=execution_info,
            standalone_mode=standalone_mode,
            skip_pip_venv_install=skip_pip_venv_install,
            first_time=first_time,
        )

        # print message so users know they can enable cache
        if not self.package_api.is_cached_enabled():
            print(
                "::: Python virtual environment cache is DISABLED. "
                "To accelerate spin-up time set `agent.venvs_cache.path=~/.cairnml/venvs-cache` :::\n"
            )

        # check if we have a cached folder
        if (
            cached_requirements
            and not skip_pip_venv_install
            and not self.poetry.enabled
            and not self.uv.enabled
            and self.package_api.get_cached_venv(
                requirements=cached_requirements,
                docker_cmd=execution_info.docker_cmd if execution_info else None,
                python_version=self.package_api.python,
                cuda_version=self._session.config.get("agent.cuda_version"),
                destination_folder=Path(venv_dir),
            )
        ):
            print("::: Using Cached environment {} :::".format(self.package_api.get_last_used_entry_cache()))
            return venv_dir, requirements_manager, True, requested_python_version

        # create the initial venv
        if not skip_pip_venv_install:
            if call_package_manager_create:
                self.package_api.create()
        else:
            if not venv_dir.exists():
                venv_dir.mkdir(parents=True, exist_ok=True)

        return venv_dir, requirements_manager, False, requested_python_version

    def _setup_package_api(
        self,
        executable_name,
        executable_version_suffix,
        venv_dir,
        execution_info,
        standalone_mode,
        skip_pip_venv_install=False,
        first_time=False,
    ):
        # type: (str, str, Path, ExecutionInfo, bool, bool, bool) -> Tuple[bool, RequirementsManager]
        requirements_manager = self._get_requirements_manager(base_interpreter=executable_name)

        package_manager_params = dict(
            session=self._session,
            python=executable_version_suffix if self.is_conda else executable_name,
            path=venv_dir,
            requirements_manager=requirements_manager,
            execution_info=execution_info,
        )

        global_package_manager_params = dict(
            interpreter=executable_name,
            session=self._session,
        )

        call_package_manager_create = False
        if not self.is_conda:
            if standalone_mode or skip_pip_venv_install:
                # pip with standalone mode
                self.global_package_api = SystemPip(**global_package_manager_params)
                if standalone_mode:
                    self.package_api = VirtualenvPip(**package_manager_params)
                else:
                    if not Path(executable_name).is_file():
                        executable_name_path = find_executable(executable_name)
                        print("Interpreter '{}' found at '{}'".format(executable_name, executable_name_path))
                        executable_name = executable_name_path
                    # we can change it, no one is going to use it anyhow
                    package_manager_params["path"] = None
                    package_manager_params["interpreter"] = executable_name
                    self.package_api = VirtualenvPip(**package_manager_params)
            else:
                if self.uv.enabled:
                    self.uv.initialize()
                    self.package_api = self.uv.get_api(**package_manager_params)
                else:
                    self.package_api = VirtualenvPip(**package_manager_params)

                if first_time:
                    self.package_api.remove()
                    call_package_manager_create = True

                self.global_package_api = SystemPip(**global_package_manager_params)
        else:
            if standalone_mode:
                # conda with standalone mode
                get_conda = partial(CondaAPI, **package_manager_params)
                self.package_api = get_conda()
            else:
                get_conda = partial(CondaAPI, **package_manager_params)
                self.package_api = get_conda()
                # no support for reusing Conda environments
                self.package_api.remove()
                call_package_manager_create = True

                if venv_dir.exists():
                    timestamp = datetime.utcnow().strftime("%Y-%m-%d-%H-%M-%S")
                    new_venv_folder = venv_dir.with_name("{}_{}".format(venv_dir.name, timestamp))
                    self.warning('Path "{}" exists, using "{}" instead'.format(venv_dir, new_venv_folder))
                    venv_dir = new_venv_folder
                    self.package_api = get_conda(path=venv_dir)

        return call_package_manager_create, requirements_manager

    def remove_non_backwards_compatible_entries(self, config: Config):
        if not self._standalone_mode or not ENV_CONFIG_BC_IN_STANDALONE.get() or self._session.feature_set == "basic":
            return
        config.pop("agent.package_manager.pip_version")  # removed due to a breaking change in v1.5.1

    def get_docker_config_cmd(self, docker_args):
        docker_image = (
            str(ENV_DOCKER_IMAGE.get() or self._session.config.get("agent.default_docker.image", "nvidia/cuda"))
            if not docker_args
            else docker_args[0]
        )
        docker_arguments = docker_image.split(" ") if docker_image else []
        if len(docker_arguments) > 1:
            docker_image = docker_arguments[0]
            docker_arguments = docker_arguments[1:]
        elif docker_args and isinstance(docker_args, list) and len(docker_args) > 1:
            docker_arguments = docker_args[1:]
        else:
            docker_arguments = self._session.config.get("agent.default_docker.arguments", None) or []
            if isinstance(docker_arguments, str):
                docker_arguments = [docker_arguments]

        # store docker arguments
        self._docker_image = docker_image
        self._docker_arguments = docker_arguments

        if docker_args:
            self._docker_default_cmd_override = True

        print(
            "Running in Docker{} mode (v19.03 and above) - using default docker image: {} {} {}\n".format(
                " *standalone*" if self._standalone_mode else "",
                self._docker_image,
                DockerArgsSanitizer.sanitize_docker_command(self._session, self._docker_arguments) or "",
                "\n(default docker commandline override, config matching rules are ignored)"
                if self._docker_default_cmd_override
                else "",
            )
        )

        temp_config = deepcopy(self._session.config)
        self.remove_non_backwards_compatible_entries(temp_config)
        mounted_cache_dir = temp_config.get("agent.docker_internal_mounts.sdk_cache", self._docker_fixed_user_cache)
        mounted_pip_dl_dir = temp_config.get(
            "agent.docker_internal_mounts.pip_download", "/root/.cairnml/pip-download-cache"
        )
        mounted_vcs_cache = temp_config.get("agent.docker_internal_mounts.vcs_cache", "/root/.cairnml/vcs-cache")
        mounted_venv_dir = temp_config.get("agent.docker_internal_mounts.venv_build", "~/.cairnml/venvs-builds")
        temp_config.put("sdk.storage.cache.default_base_dir", mounted_cache_dir)
        temp_config.put("agent.pip_download_cache.path", mounted_pip_dl_dir)
        temp_config.put("agent.vcs_cache.path", mounted_vcs_cache)
        temp_config.put("agent.package_manager.conda_env_as_base_docker", False)
        temp_config.put("agent.default_python", "")
        temp_config.put("agent.python_binary", "")
        temp_config.put("agent.cuda_version", "")
        temp_config.put("agent.cudnn_version", "")
        temp_config.put("agent.venvs_dir", mounted_venv_dir)
        temp_config.put(
            "agent.git_user", (ENV_AGENT_GIT_USER.get() or self._session.config.get("agent.git_user", None))
        )
        temp_config.put(
            "agent.git_pass", (ENV_AGENT_GIT_PASS.get() or self._session.config.get("agent.git_pass", None))
        )
        temp_config.put(
            "agent.git_host", (ENV_AGENT_GIT_HOST.get() or self._session.config.get("agent.git_host", None))
        )
        temp_config.put("agent.git", self._session.config.get("agent.git", None))

        force_system_site_packages = ENV_FORCE_SYSTEM_SITE_PACKAGES.get()
        force_system_site_packages = force_system_site_packages if force_system_site_packages is not None else True
        if force_system_site_packages:
            temp_config.put("agent.package_manager.system_site_packages", True)

        # if venvs_cache is NOT disabled,
        # we have to update the mount path and the path inside the container
        if temp_config.get("agent.venvs_cache.path", None):
            venvs_cache_path = temp_config.get("agent.docker_internal_mounts.venvs_cache", None)
            if not venvs_cache_path:
                venvs_cache_path = "/root/.cairnml/venvs-cache"
                temp_config.put("agent.docker_internal_mounts.venvs_cache", venvs_cache_path)
            # update the venvs cache path to the container scope
            temp_config.put("agent.venvs_cache.path", venvs_cache_path)

        if (ENV_SSH_AUTH_SOCK.get() or "").strip():
            self._host_ssh_cache = None
            ssh_auth_sock_env = "SSH_AUTH_SOCK={}".format(ENV_SSH_AUTH_SOCK.get())
            if not self._extra_docker_arguments or ssh_auth_sock_env not in self._extra_docker_arguments:
                self._extra_docker_arguments = (self._extra_docker_arguments or []) + [
                    "-v",
                    "{}:{}".format(ENV_SSH_AUTH_SOCK.get(), ENV_SSH_AUTH_SOCK.get()),
                    "-e",
                    ssh_auth_sock_env,
                ]
        elif ENV_AGENT_DISABLE_SSH_MOUNT.get() or self._session.config.get("agent.disable_ssh_mount", None):
            self._host_ssh_cache = None
        else:
            self._host_ssh_cache = mkdtemp(prefix="cairnml_agent.ssh.")
            self._temp_cleanup_list.append(self._host_ssh_cache)

        return temp_config, partial(self._get_docker_config_cmd, temp_config=temp_config)

    def _get_docker_config_cmd(self, temp_config, **kwargs):
        self.debug("Setting up docker config command")

        def load_path(field, default=None):
            value = self._session.config.get(field, default)
            return Path(os.path.expandvars(value)).expanduser().as_posix() if value else None

        host_cache = load_path("sdk.storage.cache.default_base_dir")
        self.debug("host_cache: {}".format(host_cache))

        host_pip_dl = load_path("agent.pip_download_cache.path")
        self.debug("host_pip_dl: {}".format(host_pip_dl))

        host_vcs_cache = load_path("agent.vcs_cache.path") if temp_config.get("agent.vcs_cache.enabled", True) else ""
        self.debug("host_vcs_cache: {}".format(host_vcs_cache))

        host_venvs_cache = load_path("agent.venvs_cache.path")
        self.debug("host_venvs_cache: {}".format(host_venvs_cache))

        host_ssh_cache = self._host_ssh_cache
        self.debug("host_ssh_cache: {}".format(host_ssh_cache))

        host_apt_cache = load_path("agent.docker_apt_cache", default="~/.cairnml/apt-cache")
        self.debug("host_apt_cache: {}".format(host_apt_cache))

        host_pip_cache = load_path("agent.docker_pip_cache", default="~/.cairnml/pip-cache")
        self.debug("host_pip_cache: {}".format(host_pip_cache))

        host_poetry_cache = (
            load_path("agent.docker_poetry_cache", "~/.cairnml/poetry-cache") if self.poetry.enabled else None
        )
        self.debug("host_poetry_cache: {}".format(host_poetry_cache))

        check_job_mounts(
            [
                host_cache,
                host_pip_dl,
                host_vcs_cache,
                host_venvs_cache,
                host_apt_cache,
                host_pip_cache,
                host_poetry_cache,
            ]
        )

        # make sure all folders are valid
        if host_apt_cache:
            Path(host_apt_cache).mkdir(parents=True, exist_ok=True)
        if host_pip_cache:
            Path(host_pip_cache).mkdir(parents=True, exist_ok=True)
        if host_poetry_cache:
            Path(host_poetry_cache).mkdir(parents=True, exist_ok=True)
        if host_cache:
            Path(host_cache).mkdir(parents=True, exist_ok=True)
        if host_pip_dl:
            Path(host_pip_dl).mkdir(parents=True, exist_ok=True)
        if host_vcs_cache:
            Path(host_vcs_cache).mkdir(parents=True, exist_ok=True)
        if host_ssh_cache:
            Path(host_ssh_cache).mkdir(parents=True, exist_ok=True)
        if host_venvs_cache:
            Path(host_venvs_cache).mkdir(parents=True, exist_ok=True)

        if host_ssh_cache:
            # copy the .ssh folder to a temp folder, to be mapped into docker
            # noinspection PyBroadException
            try:
                if Path(host_ssh_cache).is_dir():
                    shutil.rmtree(host_ssh_cache, ignore_errors=True)
                shutil.copytree(Path("~/.ssh").expanduser().as_posix(), host_ssh_cache)
            except Exception:
                # if we failed to copy / delete, let's see if we
                self.log.warning(
                    "Failed creating temporary copy of ~/.ssh for git credential, creating a new temp folder"
                )
                # noinspection PyBroadException
                try:
                    host_ssh_cache = mkdtemp(prefix="cairnml_agent.ssh.")
                    shutil.copytree(Path("~/.ssh").expanduser().as_posix(), host_ssh_cache)
                except Exception:
                    self.log.warning("Failed creating temporary copy of ~/.ssh for git credential, removing mount!")
                    host_ssh_cache = None

        # check if the .git credentials exist:
        try:
            host_git_credentials = [
                f.as_posix()
                for f in [Path("~/.git-credentials").expanduser(), Path("~/.gitconfig").expanduser()]
                if f.is_file()
            ]
        except Exception:
            host_git_credentials = None

        extra_shell_script_str = ""
        if self._extra_shell_script:
            cmds = self._extra_shell_script
            if not isinstance(cmds, (list, tuple)):
                cmds = [cmds]
            extra_shell_script_str = " ; ".join(map(str, cmds)) + " ; "

        bash_script = self._session.config.get("agent.docker_init_bash_script", None)
        preprocess_bash_script = self._session.config.get("agent.docker_preprocess_bash_script", None)
        install_opencv_libs = self._session.config.get("agent.docker_install_opencv_libs", True)

        self.temp_config_path = self.temp_config_path or safe_mkstemp(
            suffix=".cfg",
            prefix=".cairnml_agent.",
            text=True,
            name_only=True,
            dir=(ENV_TEMP_STDOUT_FILE_DIR.get() or None),
        )

        mounted_cache_dir = temp_config.get("sdk.storage.cache.default_base_dir")
        mounted_pip_dl_dir = temp_config.get("agent.pip_download_cache.path")
        mounted_vcs_cache = temp_config.get("agent.vcs_cache.path")
        mounted_venvs_cache = temp_config.get("agent.docker_internal_mounts.venvs_cache", "")
        mount_ssh = temp_config.get("agent.docker_internal_mounts.ssh_folder", None)
        mount_ssh_ro = temp_config.get("agent.docker_internal_mounts.ssh_ro_folder", None)
        mount_apt_cache = temp_config.get("agent.docker_internal_mounts.apt_cache", None)
        mount_pip_cache = temp_config.get("agent.docker_internal_mounts.pip_cache", None)
        mount_poetry_cache = temp_config.get("agent.docker_internal_mounts.poetry_cache", None)

        # Make sure we have created the configuration file for the executor
        if not self.dump_config(self.temp_config_path, config=temp_config):
            self.log.warning("Could not update docker configuration file {}".format(self.temp_config_path))

        docker_cmd = dict(
            worker_id=self.worker_id,
            parent_worker_id=self.parent_worker_id or self.worker_id,
            # docker_image=docker_image,
            # docker_arguments=docker_arguments,
            extra_docker_arguments=self._extra_docker_arguments,
            extra_shell_script=extra_shell_script_str,
            python_version="python3",
            conf_file=self.temp_config_path,
            host_apt_cache=host_apt_cache,
            host_pip_cache=host_pip_cache,
            host_poetry_cache=host_poetry_cache,
            host_ssh_cache=host_ssh_cache,
            host_git_credentials=host_git_credentials,
            host_cache=host_cache,
            mounted_cache=mounted_cache_dir,
            host_pip_dl=host_pip_dl,
            mounted_pip_dl=mounted_pip_dl_dir,
            host_vcs_cache=host_vcs_cache,
            mounted_vcs_cache=mounted_vcs_cache,
            host_venvs_cache=host_venvs_cache,
            mounted_venvs_cache=mounted_venvs_cache,
            standalone_mode=self._standalone_mode,
            force_current_version=self._force_current_version,
            bash_script=bash_script,
            preprocess_bash_script=preprocess_bash_script,
            install_opencv_libs=install_opencv_libs,
            mount_ssh=mount_ssh,
            mount_ssh_ro=mount_ssh_ro,
            mount_apt_cache=mount_apt_cache,
            mount_pip_cache=mount_pip_cache,
            mount_poetry_cache=mount_poetry_cache,
        )

        docker_cmd.update(kwargs)
        return self._get_docker_cmd(DockerRun(**docker_cmd))

    def _get_child_agents_count_for_worker(self):
        """Get the amount of running child agents. In case of any error return 0"""
        parent_worker_label = self._parent_worker_label.format(self.worker_id)

        default_cmd = "docker ps --filter label={parent_worker_label} --format {{{{.ID}}}}"
        child_agents_cmd = ENV_CHILD_AGENTS_COUNT_CMD.get() or default_cmd

        cmd = shlex.split(child_agents_cmd.format(parent_worker_label=parent_worker_label))
        try:
            output = Argv(*cmd).get_output(stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as ex:
            self.log.warning("error getting child agents: %s", ex)
            return 0

        return len(output.splitlines()) if output else 0

    @staticmethod
    def _amd_gpu_docker_args(visible=None):
        # AMD render nodes live at /dev/dri/renderD{128+N} for GPU N.
        args = ["--device=/dev/kfd"]
        if visible:
            indices = [tok.strip() for tok in str(visible).split(",") if tok.strip()]
            for idx in indices:
                try:
                    n = int(idx)
                except ValueError:
                    continue
                args.append("--device=/dev/dri/renderD{}".format(128 + n))
            joined = ",".join(indices)
            if joined:
                args += ["-e", "HIP_VISIBLE_DEVICES={}".format(joined), "-e", "ROCR_VISIBLE_DEVICES={}".format(joined)]
        else:
            try:
                render_nodes = sorted(name for name in os.listdir("/dev/dri") if name.startswith("renderD"))
            except OSError:
                render_nodes = []
            for name in render_nodes:
                args.append("--device=/dev/dri/{}".format(name))
        args += ["--group-add", "video"]
        return args

    def _filter_docker_args(self, docker_args):
        # type: (List[str]) -> List[str]
        """
        Filter docker args matching specific flags.
        Supports list of Regular expressions, e.g self._docker_args_filters = ["^--env$", "^-e$"]

        :argument docker_args: List of docker argument strings (flags and values)
        """
        # if no filtering, do nothing
        if not docker_args or not self._docker_args_filters:
            return docker_args

        args = docker_args[:]
        results = []
        while args:
            cmd = args.pop(0).strip()
            if any(f.match(cmd) for f in self._docker_args_filters):
                results.append(cmd)
                if "=" not in cmd and args and not args[0].startswith("-"):
                    try:
                        results.append(args.pop(0).strip())
                    except IndexError:
                        pass
        return results

    @staticmethod
    def _resolve_docker_env_args(docker_args):
        # type: (List[str]) -> List[str]
        """
        Resolve -e / --env docker environment args matching $VAR or ${VAR} from the host environment

        :argument docker_args: List of docker argument strings (flags and values)
        """
        non_list_args = (
            "rm",
            "read-only",
            "sig-proxy",
            "tty",
            "privileged",
            "publish-all",
            "interactive",
            "init",
            "help",
            "detach",
        )
        non_list_args_single = (
            "t",
            "P",
            "i",
            "d",
        )

        # if no filtering, do nothing
        if not docker_args:
            return docker_args

        args = docker_args[:]
        skip_arg = False
        for i, cmd in enumerate(docker_args):
            if skip_arg and not cmd.startswith("-"):
                continue

            skip_arg = False

            if cmd.startswith("--"):
                # jump over single command
                if cmd[2:] in non_list_args:
                    continue
            elif cmd.startswith("-"):
                # jump over single character non args
                if cmd[1:] in non_list_args_single:
                    continue

            # if we are here we have a command to bypass and the list after it
            if cmd in ("-e", "--env"):
                skip_arg = True
                for j in range(i + 1, len(args)):
                    if args[j].startswith("-"):
                        break

                    parts = args[j].split("=", 1)
                    if len(parts) != 2:
                        continue

                    args[j] = "{}={}".format(parts[0], os.path.expandvars(parts[1]))

            elif cmd.startswith("-"):
                skip_arg = True

        return args

    def _get_docker_cmd(self, run):
        """Build the docker run command of a task from a DockerRun, one stage after another."""
        self.debug("Constructing docker command", context="docker")
        docker = "docker"
        base_cmd = [docker, "run", "-t"]
        run.use_rm = True
        self._docker_restart_args(run, base_cmd)
        self._docker_gpu_args(run, base_cmd)
        self._docker_user_args(run, base_cmd)
        self._docker_labels_and_ports(run, base_cmd)
        self._docker_loopback_network(run, base_cmd)
        self._docker_kubernetes_args(run, base_cmd)
        self._docker_host_folders(run, base_cmd)
        self._docker_env_args(run, base_cmd)
        self._docker_wheel_args(run, base_cmd)
        self._docker_mount_defaults(run, base_cmd)
        self._docker_standalone_script(run, base_cmd)
        self._docker_setup_script(run, base_cmd)
        self._docker_log_mounts(run, base_cmd)
        self._docker_bootstrap_args(run, base_cmd)
        self._docker_mount_args(run, base_cmd)
        self._docker_command_tail(run, base_cmd)
        return base_cmd

    def _docker_restart_args(self, run, base_cmd):
        """restart part of the docker run command (see _get_docker_cmd)."""
        if run.restart:
            if run.restart in ("unless-stopped", "no", "always") or run.restart.startswith("on-failure"):
                base_cmd += ["--restart", run.restart]
                run.use_rm = False
            else:
                self.log.error('Invalid restart value "{}" , ignoring'.format(run.restart))

    def _docker_gpu_args(self, run, base_cmd):
        """gpu part of the docker run command (see _get_docker_cmd)."""
        run.update_scheme = ""
        run.dockers_nvidia_visible_devices = "all"
        gpu_devices = Session.get_nvidia_visible_env()
        if gpu_devices is None or gpu_devices.lower().strip() == "all":
            if ENV_DOCKER_SKIP_GPUS_FLAG.get():
                run.dockers_nvidia_visible_devices = (
                    Session.get_nvidia_visible_env() or run.dockers_nvidia_visible_devices
                )
            elif detect_rocm_env():
                base_cmd += self._amd_gpu_docker_args()
            else:
                base_cmd += [
                    "--gpus",
                    "all",
                ]
        elif gpu_devices.strip() and gpu_devices.strip() != "none":
            if ENV_DOCKER_SKIP_GPUS_FLAG.get():
                run.dockers_nvidia_visible_devices = gpu_devices
            elif detect_rocm_env():
                base_cmd += self._amd_gpu_docker_args(visible=gpu_devices)
            else:
                # replace back "." to ":" MIG support
                if self._use_podman:
                    base_cmd += [
                        "--gpus",
                        str(gpu_devices.replace(".", ":")),
                    ]
                else:
                    base_cmd += [
                        "--gpus",
                        '"device={}"'.format(gpu_devices.replace(".", ":")),
                    ]
            # We are using --gpu, so we should not pass NVIDIA_VISIBLE_DEVICES, I think.
            # base_cmd += ['-e', 'NVIDIA_VISIBLE_DEVICES=' + gpu_devices, ]
        elif gpu_devices.strip() == "none":
            run.dockers_nvidia_visible_devices = gpu_devices

    def _docker_user_args(self, run, base_cmd):
        """args part of the docker run command (see _get_docker_cmd)."""
        if run.docker_arguments:
            run.docker_arguments = (
                list(run.docker_arguments)
                if isinstance(run.docker_arguments, (list, tuple))
                else [run.docker_arguments]
            )
            run.docker_arguments = self._filter_docker_args(run.docker_arguments)
            if self._session.config.get("agent.docker_allow_host_environ", None):
                run.docker_arguments = self._resolve_docker_env_args(run.docker_arguments)

        if run.extra_docker_arguments:
            # we always resolve environments in the `extra_docker_arguments` because the admin set them (not users)
            run.extra_docker_arguments = self._resolve_docker_env_args(run.extra_docker_arguments)
            run.extra_docker_arguments = (
                [run.extra_docker_arguments]
                if isinstance(run.extra_docker_arguments, str)
                else run.extra_docker_arguments
            )

        # decide on order of docker args when merging overlapping arguments
        # from extra_docker_args and the Task's docker_args
        merged, stripped_switches = DockerArgsSanitizer.merge_docker_args(
            config=self._session.config,
            task_docker_arguments=run.docker_arguments,
            extra_docker_arguments=run.extra_docker_arguments,
        )
        base_cmd += merged

        if stripped_switches:
            warn_msg = (
                "cairnml-agent: ignoring docker argument(s) {}; "
                "they would give the task access to the host or override the agent startup script"
            ).format(", ".join(("-" if len(s) == 1 else "--") + s for s in stripped_switches))
            self.log.warning(warn_msg)
            if run.env_task_id:
                self.send_logs(run.env_task_id, [warn_msg + "\n"], session=self._session)

    def _docker_labels_and_ports(self, run, base_cmd):
        """ports part of the docker run command (see _get_docker_cmd)."""
        # check if we have mapped ports
        try:
            port_mappings_struct = DockerArgsSanitizer.resolve_port_mapping(
                config=self._session.config, docker_arguments=base_cmd
            )
            if port_mappings_struct:
                new_docker_cmd, additional_task_runtime = port_mappings_struct
                if new_docker_cmd:
                    base_cmd = new_docker_cmd
                if additional_task_runtime:
                    from cairnml_agent.helper.task_runtime import TaskRuntime

                    task_runtime = TaskRuntime(self._session)
                    task_runtime.update_task_runtime(run.env_task_id, additional_task_runtime)
        except Exception as e:
            self.log.warning("WARNING: Failed parsing port mapping requested for docker execution: {}".format(e))

        # set docker labels
        base_cmd += ["-l", self._worker_label.format(run.worker_id)]
        base_cmd += ["-l", self._parent_worker_label.format(run.parent_worker_id)]

        extra_labels = ENV_EXTRA_DOCKER_LABELS.get()
        for label in extra_labels or []:
            base_cmd += ["-l", label]

        self.debug("Command: {}".format(base_cmd), context="docker")

    def _docker_loopback_network(self, run, base_cmd):
        """loop part of the docker run command (see _get_docker_cmd)."""
        # A server on this machine's loopback address is reachable only from the host's network, so the job container
        # shares it. The rule follows from the address; there is no setting for it.
        if self._server_is_loopback() and not any(a.strip().startswith("--network") for a in base_cmd):
            base_cmd += ["--network", "host"]

    def _docker_kubernetes_args(self, run, base_cmd):
        """k8s part of the docker run command (see _get_docker_cmd)."""
        # check if running inside a kubernetes
        if ENV_DOCKER_HOST_MOUNT.get() or (
            os.environ.get("KUBERNETES_SERVICE_HOST") and os.environ.get("KUBERNETES_PORT")
        ):
            # map network to sibling docker, unless we have other network argument
            if not any(a.strip().startswith("--network") for a in base_cmd):
                # noinspection PyBroadException
                try:
                    network_mode = get_bash_output(
                        "docker inspect --format='{{.HostConfig.NetworkMode}}' $(basename $(cat /proc/1/cpuset))"
                    )
                    if network_mode:
                        base_cmd += ["--network", network_mode]
                except Exception:
                    pass
            base_cmd += ["-e", "NVIDIA_VISIBLE_DEVICES={}".format(run.dockers_nvidia_visible_devices)]

            self.debug("Running in k8s: {}".format(base_cmd), context="docker")

    def _docker_host_folders(self, run, base_cmd):
        """hostm part of the docker run command (see _get_docker_cmd)."""
        # check if we need to map host folders
        if ENV_DOCKER_HOST_MOUNT.get():
            # expect CAIRNML_AGENT_K8S_HOST_MOUNT = '/mnt/host/data:/root/.cairnml'
            k8s_node_mnt, _, k8s_pod_mnt = ENV_DOCKER_HOST_MOUNT.get().partition(":")
            # search and replace all the host folders with the k8s
            host_mounts = [
                run.host_apt_cache,
                run.host_pip_cache,
                run.host_poetry_cache,
                run.host_pip_dl,
                run.host_cache,
                run.host_vcs_cache,
                run.host_venvs_cache,
            ]
            self.debug("Mapping host mounts: {}".format(host_mounts), context="docker")
            for i, m in enumerate(host_mounts):
                if not m:
                    continue
                if k8s_pod_mnt not in m:
                    print("Warning: K8S mount missing, ignoring cached folder {}".format(m))
                    host_mounts[i] = None
                else:
                    host_mounts[i] = m.replace(k8s_pod_mnt, k8s_node_mnt, 1)
            self.debug("Mapped host mounts: {}".format(host_mounts), context="docker")
            (
                run.host_apt_cache,
                run.host_pip_cache,
                run.host_poetry_cache,
                run.host_pip_dl,
                run.host_cache,
                run.host_vcs_cache,
                run.host_venvs_cache,
            ) = host_mounts

            # copy the configuration file into the mounted folder
            new_conf_file = os.path.join(k8s_pod_mnt, ".cairnml_agent.{}.cfg".format(quote(run.worker_id, safe="")))
            try:
                rm_tree(new_conf_file)
                rm_file(new_conf_file)
                shutil.copy(run.conf_file, new_conf_file)
                run.conf_file = new_conf_file.replace(k8s_pod_mnt, k8s_node_mnt)
            except Exception as ex:
                raise ValueError("Error: could not copy configuration file into: {}".format(new_conf_file)) from ex

            self.debug("Config file target: {}, host: {}".format(new_conf_file, run.conf_file), context="docker")

            if run.host_ssh_cache:
                new_ssh_cache = os.path.join(k8s_pod_mnt, ".cairnml_agent.{}.ssh".format(quote(run.worker_id, safe="")))
                try:
                    rm_tree(new_ssh_cache)
                    shutil.copytree(run.host_ssh_cache, new_ssh_cache)
                    run.host_ssh_cache = new_ssh_cache.replace(k8s_pod_mnt, k8s_node_mnt)
                except Exception as ex:
                    raise ValueError("Error: could not copy .ssh directory into: {}".format(new_ssh_cache)) from ex
                self.debug(
                    "Copied host SSH cache to: {}, host {}".format(new_ssh_cache, run.host_ssh_cache), context="docker"
                )

    def _docker_env_args(self, run, base_cmd):
        """env part of the docker run command (see _get_docker_cmd)."""
        base_cmd += [
            "-e",
            "CAIRNML_WORKER_ID=" + run.worker_id,
        ]
        # update the docker image, so the system knows where it runs
        base_cmd += ["-e", "CAIRNML_DOCKER_IMAGE={}".format(run.docker_image)]

        if run.env_task_id:
            base_cmd += [
                "-e",
                "CAIRNML_TASK_ID={}".format(run.env_task_id),
            ]

        if run.auth_token:
            # if auth token is passed then put it in the env var
            base_cmd += ["-e", "{}={}".format(ENV_AGENT_AUTH_TOKEN.vars[0], run.auth_token)]

        if run.job_token_dir:
            # the job's token file, renewed by the agent while the job runs; the container reads it from its folder
            base_cmd += [
                "-v",
                "{}:{}".format(run.job_token_dir, CONTAINER_DIR),
                "-e",
                "CAIRNML_JOB_TOKEN_FILE={}/{}".format(CONTAINER_DIR, TOKEN_FILE_NAME),
            ]

        if run.worker_tags:
            base_cmd += [
                "-e",
                "{}={}".format(ENV_WORKER_TAGS.vars[0], " ".join(shlex.quote(t) for t in run.worker_tags)),
            ]

        skip_pip_venv_install = ENV_AGENT_SKIP_PIP_VENV_INSTALL.get()
        if skip_pip_venv_install:
            base_cmd += ["-e", "{}={}".format(ENV_AGENT_SKIP_PIP_VENV_INSTALL.vars[0], skip_pip_venv_install)]

        if self._services_mode:
            base_cmd += ["-e", "CAIRNML_AGENT_SERVICE_TASK=1"]

    def _docker_wheel_args(self, run, base_cmd):
        """wheel part of the docker run command (see _get_docker_cmd)."""
        # if we are running a RC version, install the same version in the docker
        # because the default latest, will be a release version (not RC)
        # The job container installs this agent from a wheel of its own source tree, and never from PyPI.
        # There is no setting for it: the container runs the same agent as the one that starts it.
        local_wheel = local_agent_wheel()
        docker_wheel = "/tmp/{}".format(basename(local_wheel))
        base_cmd += ["-v", local_wheel + ":" + docker_wheel]
        run.cairnml_agent_wheel = docker_wheel if self._bootstrap else '"{}"'.format(docker_wheel)
        sdk_wheel = local_sdk_wheel()
        docker_sdk_wheel = "/tmp/{}".format(basename(sdk_wheel))
        base_cmd += [
            "-v",
            sdk_wheel + ":" + docker_sdk_wheel,
            "-e",
            "{}={}".format(LOCAL_SDK_WHEEL_ENV, docker_sdk_wheel),
        ]

    def _docker_mount_defaults(self, run, base_cmd):
        """mdef part of the docker run command (see _get_docker_cmd)."""
        run.mount_ssh = run.mount_ssh or "/root/.ssh"
        run.mount_ssh_ro = run.mount_ssh_ro or "{}_ro".format(run.mount_ssh.rstrip("/"))
        run.mount_apt_cache = run.mount_apt_cache or "/var/cache/apt/archives"
        run.mount_pip_cache = run.mount_pip_cache or "/root/.cache/pip"
        run.mount_poetry_cache = run.mount_poetry_cache or "/root/.cache/pypoetry"
        run.mount_git_ro = "{}.git".format(run.mount_ssh_ro.rstrip("/"))

    def _docker_standalone_script(self, run, base_cmd):
        """std part of the docker run command (see _get_docker_cmd)."""
        if not run.standalone_mode and not self._bootstrap:
            if not run.bash_script:
                # Find the highest python version installed, or install from apt-get
                # python+pip is the requirement to match
                run.bash_script = [
                    "echo 'Binary::apt::APT::Keep-Downloaded-Packages \"true\";' > /etc/apt/apt.conf.d/docker-clean",
                    "chown -R $(whoami) $HOME/.cache/pip",
                    "export DEBIAN_FRONTEND=noninteractive",
                    'export CAIRNML_APT_INSTALL="$CAIRNML_APT_INSTALL{}"'.format(
                        " libsm6 libxext6 libxrender-dev libglib2.0-0" if run.install_opencv_libs else ""
                    ),
                    "cp -Rf {mount_ssh_ro} -T {mount_ssh}" if run.host_ssh_cache else "",
                    "cp -Rf {mount_git_ro} -T ~/" if run.host_git_credentials else "",
                    '[ ! -z $(which git 2> /dev/null || command -v git) ] || export CAIRNML_APT_INSTALL="$CAIRNML_APT_INSTALL git"',
                    "declare LOCAL_PYTHON",
                    "[ ! -z $LOCAL_PYTHON ] || for i in {{20..5}}; do (which {python_single_digit}.$i 2> /dev/null || command -v {python_single_digit}.$i) && "
                    + "{python_single_digit}.$i -m pip --version && "
                    + "export LOCAL_PYTHON=$(which {python_single_digit}.$i 2> /dev/null || command -v {python_single_digit}.$i) && break ; done",
                    '[ ! -z $LOCAL_PYTHON ] || export CAIRNML_APT_INSTALL="$CAIRNML_APT_INSTALL {python_single_digit}-pip"',  # noqa
                    '[ -z "$CAIRNML_APT_INSTALL" ] || '
                    "(apt-get update -y ; apt-get install -y $CAIRNML_APT_INSTALL) || "
                    "(dnf install -y $CAIRNML_APT_INSTALL)",
                    "rm -f /usr/lib/python3.*/EXTERNALLY-MANAGED",  # remove PEP 668
                ]

            if run.preprocess_bash_script:
                run.bash_script = run.preprocess_bash_script + run.bash_script

            docker_bash_script = (
                " ; ".join([line for line in run.bash_script if line])
                if not isinstance(run.bash_script, str)
                else run.bash_script
            )

            # make sure that if we do not have $LOCAL_PYTHON defined, we set it to python3
            # notice that if $LOCAL_PYTHON -m pip fails, that means we might have a broken python in the path
            # so we set to default path and try to set global python
            run.update_scheme += (
                docker_bash_script
                + " ; "
                + "[ ! -z $LOCAL_PYTHON ] || export LOCAL_PYTHON={python} ; "
                + "$LOCAL_PYTHON -m pip --version > /dev/null || export LOCAL_PYTHON=$(PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin command -v python3) ; "
                + "$LOCAL_PYTHON -m pip install -U {pip_version} ; "
                # The job installs the SDK of this machine first, so pip never takes cairnml from PyPI for the
                # agent's requirement (the agent wheel asks for cairnml<0.1).
                + '$LOCAL_PYTHON -m pip install -U "$CAIRNML_AGENT_LOCAL_SDK_WHEEL" ; '
                + "$LOCAL_PYTHON -m pip install -U {cairnml_agent_wheel} ; "
            ).format(
                python_single_digit=run.python_version.split(".")[0],
                python=run.python_version,
                pip_version=" ".join(PackageManager.get_pip_versions(wrap='"')),
                cairnml_agent_wheel=run.cairnml_agent_wheel,
                mount_ssh_ro=run.mount_ssh_ro,
                mount_ssh=run.mount_ssh,
                mount_git_ro=run.mount_git_ro,
            )

    def _docker_setup_script(self, run, base_cmd):
        """git part of the docker run command (see _get_docker_cmd)."""
        if run.host_git_credentials:
            for git_credentials in run.host_git_credentials:
                base_cmd += ["-v", "{}:{}/{}".format(git_credentials, run.mount_git_ro, Path(git_credentials).name)]

        if run.docker_bash_setup_script and run.docker_bash_setup_script.strip("\n "):
            run.extra_shell_script = (
                (run.extra_shell_script or "")
                + " ; ".join(
                    line.strip()
                    for line in run.docker_bash_setup_script.split("\n")
                    if line.strip() and not line.lstrip().startswith("#")
                )
                + " ; "
            )

    def _docker_log_mounts(self, run, base_cmd):
        """dbg part of the docker run command (see _get_docker_cmd)."""
        self.debug(
            "Adding mounts: host_ssh_cache={}, host_apt_cache={}, host_pip_cache={}, host_poetry_cache={}, "
            "host_pip_dl={}, host_cache={}, host_vcs_cache={}, host_venvs_cache={}".format(
                run.host_ssh_cache,
                run.host_apt_cache,
                run.host_pip_cache,
                run.host_poetry_cache,
                run.host_pip_dl,
                run.host_cache,
                run.host_vcs_cache,
                run.host_venvs_cache,
            ),
            context="docker",
        )

    def _docker_bootstrap_args(self, run, base_cmd):
        """boot part of the docker run command (see _get_docker_cmd)."""
        if self._bootstrap:
            base_cmd += bootstrap.get_bootstrap_docker_configuration(config=self._session.config)

            if run.preprocess_bash_script:
                preprocess_bash_script_str = (
                    "\n".join([line for line in run.preprocess_bash_script if line])
                    if not isinstance(run.preprocess_bash_script, str)
                    else run.preprocess_bash_script
                )

                base_cmd += [
                    "-e",
                    "CAIRNML_BOOTSTRAP_CUSTOM_CMD_BASE64={}".format(
                        base64.b64encode(preprocess_bash_script_str.encode("ascii")).decode("ascii")
                    ),
                ]

            if run.extra_shell_script:
                base_cmd += ["-e", "CAIRNML_PRE_CUSTOM_CMD={}".format(run.extra_shell_script)]

            if run.cairnml_agent_wheel:
                base_cmd += ["-e", "CAIRNML_AGENT_VERSION={}".format(run.cairnml_agent_wheel)]

    def _docker_mount_args(self, run, base_cmd):
        """mounts part of the docker run command (see _get_docker_cmd)."""
        base_cmd += (
            (["--name", run.name] if run.name else [])
            + ["-v", run.conf_file + ":" + DOCKER_ROOT_CONF_FILE]
            + ["-e", "CAIRNML_CONFIG_FILE={}".format(DOCKER_ROOT_CONF_FILE)]
            + (["-v", run.host_ssh_cache + ":" + run.mount_ssh_ro] if run.host_ssh_cache else [])
            + (["-v", run.host_apt_cache + ":" + run.mount_apt_cache] if run.host_apt_cache else [])
            + (["-v", run.host_pip_cache + ":" + run.mount_pip_cache] if run.host_pip_cache else [])
            + (["-v", run.host_poetry_cache + ":" + run.mount_poetry_cache] if run.host_poetry_cache else [])
            + (["-v", run.host_pip_dl + ":" + run.mounted_pip_dl] if run.host_pip_dl else [])
            + (["-v", run.host_cache + ":" + run.mounted_cache] if run.host_cache else [])
            + (["-v", run.host_vcs_cache + ":" + run.mounted_vcs_cache] if run.host_vcs_cache else [])
            + (["-v", run.host_venvs_cache + ":" + run.mounted_venvs_cache] if run.host_venvs_cache else [])
            + (["--rm"] if run.use_rm else [])
        )

    def _docker_command_tail(self, run, base_cmd):
        """tail part of the docker run command (see _get_docker_cmd)."""
        if self._bootstrap:
            base_cmd += [run.docker_image, "bash", "-c", "$CAIRNML_BOOTSTRAP_DIR/bootstrap.sh "]
        else:
            base_cmd += [
                run.docker_image,
                "bash",
                "-c",
                run.update_scheme
                + run.extra_shell_script
                + "cp {} {} ; ".format(DOCKER_ROOT_CONF_FILE, DOCKER_DEFAULT_CONF_FILE)
                + "NVIDIA_VISIBLE_DEVICES={nv_visible} $LOCAL_PYTHON -u -m cairnml_agent ".format(
                    nv_visible=run.dockers_nvidia_visible_devices
                ),
            ]

    def _run_as_user_patch(self, command, trains_conf, script_dir, venv_folder, sdk_cache_folder, user_uid):
        class RunasArgv(Argv):
            def __init__(self, *args):
                super(RunasArgv, self).__init__(*args)
                self.uid = 0
                self.gid = 0

            def call_subprocess(self, func, censor_password=False, *args, **kwargs):
                self._log.debug("running: %s: %s", func.__name__, list(self))
                with self.normalize_exception(censor_password):
                    return func(list(self), *args, preexec_fn=self._change_uid, **kwargs)

            def set_uid(self, user_uid, user_gid):
                from pwd import getpwnam

                try:
                    self.uid = getpwnam(user_uid).pw_uid
                    self.gid = getpwnam(user_gid).pw_gid
                except Exception as ex:
                    raise ValueError("Could not find requested user uid={} gid={}".format(user_uid, user_gid)) from ex

            def _change_uid(self):
                os.setgid(self.gid)
                os.setuid(self.uid)

        # create a home folder for our user
        try:
            home_folder = self._run_as_user_home
            rm_tree(home_folder)
            Path(home_folder).mkdir(parents=True, exist_ok=True)
        except Exception:
            home_folder = os.path.join("/home", self._run_as_user_home)
            rm_tree(home_folder)
            Path(home_folder).mkdir(parents=True, exist_ok=True)

        # move our entire venv into the new home
        venv_folder = venv_folder.as_posix()
        if not venv_folder.endswith(os.path.sep):
            venv_folder += os.path.sep
        new_venv_folder = os.path.join(home_folder, "venv/")
        shutil.move(venv_folder, new_venv_folder)
        # allow everyone to access it
        for f in Path(new_venv_folder).rglob("*"):
            try:
                f.chmod(0o0777)
            except Exception:
                pass
        # make sure we will be able to access the cache folder (we assume we have the ability change mod)
        if sdk_cache_folder:
            sdk_cache_folder = Path(os.path.expandvars(sdk_cache_folder)).expanduser().absolute()
            try:
                sdk_cache_folder.chmod(0o0777)
            except Exception:
                pass
            for f in sdk_cache_folder.rglob("*"):
                try:
                    f.chmod(0o0777)
                except Exception:
                    pass

        # make sure we could access the trains_conf file
        try:
            user_trains_conf = os.path.join(home_folder, "cairnml.conf")
            shutil.copy(trains_conf, user_trains_conf)
            Path(user_trains_conf).chmod(0o0777)
        except Exception:
            user_trains_conf = trains_conf

        # patch venv folder to new location
        script_dir = script_dir.replace(venv_folder, new_venv_folder)
        # New command line execution
        command = RunasArgv(
            "bash",
            "-c",
            'HOME="{}" PATH="{}" PYTHONPATH="{}" CAIRNML_CONFIG_FILE={} {}'.format(
                home_folder,
                os.environ.get("PATH", "").replace(venv_folder, new_venv_folder),
                os.environ.get("PYTHONPATH", "").replace(venv_folder, new_venv_folder),
                user_trains_conf,
                command.serialize().replace(venv_folder, new_venv_folder),
            ),
        )
        command.set_uid(user_uid=user_uid, user_gid=user_uid)

        return command, script_dir

    def _kill_daemon(self, worker_id=None):
        if not worker_id:
            worker_id, worker_name = self._generate_worker_id_name()
        else:
            worker_name = worker_id

        # Iterate over all running process
        for pid, uid, slot, file in sorted(Singleton.get_running_pids(), key=lambda x: x[1] or ""):
            if pid < 0 or uid is None:
                continue

            # either we have a match for the worker_id or we just pick the first one, and kill it.
            if (worker_id and uid == worker_id) or (not worker_id and uid.startswith("{}:".format(worker_name))):
                # this is us kill it
                print("Terminating cairnml-agent worker_id={} pid={}".format(uid, pid))
                timeout = 120 if uid.startswith("{}:dgpu".format(worker_name)) else 10
                if not terminate_process(pid, timeout=timeout):
                    error("Could not terminate process pid={}".format(pid))
                return True

        print(
            "Could not find a running cairnml-agent instance with worker_name={} worker_id={}".format(
                worker_name, worker_id
            )
        )
        return False

    def _singleton(self):
        # ensure singleton
        worker_id, worker_name = self._generate_worker_id_name()

        # if we are running in services mode, we allow double register since
        # docker-compose will kill instances before they cleanup
        self.worker_id, worker_slot = Singleton.register_instance(
            unique_worker_id=worker_id,
            worker_name=worker_name,
            api_client=self._session.api_client,
            allow_double=bool(ENV_DOCKER_HOST_MOUNT.get()),  # and bool(self._services_mode),
        )
        #  set the parent ID the first time we have a worker ID (it might change for services-mode / dgpus)
        if not self.parent_worker_id:
            self.parent_worker_id = self.worker_id

        if self.worker_id is None:
            error("Instance with the same WORKER_ID [{}] is already running".format(worker_id))
            sys.exit(1)
        # update folders based on free slot
        self._session.create_cache_folders(slot_index=worker_slot)

    def _generate_worker_id_name(self):
        worker_id = self._session.config["agent.worker_id"]
        worker_name = self._session.config["agent.worker_name"]
        if not worker_id and Session.get_nvidia_visible_env() is not None:
            nvidia_visible_devices = Session.get_nvidia_visible_env()
            if nvidia_visible_devices and nvidia_visible_devices.lower() != "none":
                worker_id = "{}:gpu{}".format(worker_name, nvidia_visible_devices)
            elif nvidia_visible_devices == "":
                pass
            else:
                worker_name = "{}:cpu".format(worker_name)
        return worker_id, worker_name

    def _resolve_queue_names(self, queues):
        if not queues and self._session.config.get("agent.queue", None):
            # the agent's own queue, made when it was enrolled: the one queue it serves
            return [self._session.config.get("agent.queue")]
        if not queues:
            # try to look for queues with "default" tag
            try:
                default_queue = self._session.send_api(queues_api.GetDefaultRequest())
                return [default_queue.id]
            except APIError:
                # if we cannot find one with "default" tag, look for a queue named "default"
                queues = ["default"]

        queues = return_list(queues)
        return [
            self._resolve_name(q if isinstance(q, str) else q.name, service="queues", search_hidden=True)
            for q in queues
        ]

    @staticmethod
    def _valid_docker_container_name(name):
        # type: (str) -> bool
        return re.fullmatch(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]+$", name) is not None

    @staticmethod
    def _get_path(d, *path, default=None):
        try:
            return functools.reduce(lambda a, b: a[b], path, d)
        except (IndexError, KeyError):
            return default


if __name__ == "__main__":
    pass

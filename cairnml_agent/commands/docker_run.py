"""The arguments of one docker command for a task, as the worker builds them."""

from dataclasses import dataclass
from typing import Any


@dataclass
class DockerRun:
    """Everything the worker needs to build the docker run command of a task (see Worker._get_docker_cmd)."""

    worker_id: Any
    parent_worker_id: Any
    docker_image: Any
    docker_arguments: Any
    python_version: Any
    conf_file: Any
    host_apt_cache: Any
    host_pip_cache: Any
    host_poetry_cache: Any
    host_ssh_cache: Any
    host_cache: Any
    mounted_cache: Any
    host_pip_dl: Any
    mounted_pip_dl: Any
    host_vcs_cache: Any
    mounted_vcs_cache: Any
    host_venvs_cache: Any
    mounted_venvs_cache: Any
    standalone_mode: Any = False
    extra_docker_arguments: Any = None
    extra_shell_script: Any = None
    force_current_version: Any = None
    host_git_credentials: Any = None
    bash_script: Any = None
    preprocess_bash_script: Any = None
    install_opencv_libs: Any = None
    docker_bash_setup_script: Any = None
    auth_token: Any = None
    job_token_dir: Any = None
    worker_tags: Any = None
    name: Any = None
    mount_ssh: Any = None
    mount_ssh_ro: Any = None
    mount_apt_cache: Any = None
    mount_pip_cache: Any = None
    mount_poetry_cache: Any = None
    env_task_id: Any = None
    restart: Any = None
    # working values, set while the command is built (see Worker._get_docker_cmd)
    use_rm: Any = None
    dockers_nvidia_visible_devices: Any = None
    update_scheme: Any = None
    cairnml_agent_wheel: Any = None
    mount_git_ro: Any = None

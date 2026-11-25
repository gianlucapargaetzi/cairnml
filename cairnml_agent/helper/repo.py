import abc
import atexit
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from base64 import b64encode
from os import environ
from random import random
from threading import Lock
from typing import Text, TypeVar, Tuple, Optional, Sequence, Mapping, Iterable, Callable  # noqa: F401  # named by type comments

import attr
from furl import furl
from pathlib import Path


from cairnml_agent.definitions import (
    ENV_AGENT_GIT_USE_MS_ENTRA_TOKEN,
    ENV_AGENT_GIT_USE_AZURE_PAT,
    ENV_GIT_CLONE_VERBOSE,
)
from cairnml_agent.helper.console import ensure_text, ensure_binary
from cairnml.backend_api.services import devices as devices_api
from cairnml_agent.errors import CommandFailedError
from cairnml_agent.helper.base import (
    select_for_platform,
    rm_tree,
    normalize_path,
    create_file_if_not_exists,
    safe_remove_file,
    ExecutionInfo,  # noqa: F401  # named by type comments
)
from cairnml_agent.helper.hash import md5
from cairnml_agent.helper.os.locks import FileLock
from cairnml_agent.helper.process import DEVNULL, Argv, COMMAND_SUCCESS, find_executable, PathLike  # noqa: F401  # named by type comments
from cairnml_agent.session import Session  # noqa: F401  # named by type comments


class VcsFactory(object):
    """
    Creates VCS instances
    """

    GIT_SUFFIX = ".git"

    @classmethod
    def create(cls, session, execution_info, location):
        # type: (Session, ExecutionInfo, PathLike) -> VCS
        """
        Create a VCS instance for config and url
        :param session: program session
        :param execution_info: task ExecutionInfo
        :param location: (desired) clone location
        """
        url = execution_info.repository
        # We only support git, hg is deprecated
        is_git = True
        # is_git = url.endswith(cls.GIT_SUFFIX)
        vcs_cls = Git if is_git else Hg
        revision = (
            execution_info.version_num
            or execution_info.tag
            or vcs_cls.remote_branch_name(execution_info.branch or vcs_cls.main_branch)
        )
        return vcs_cls(session, url, location, revision)


# noinspection PyUnresolvedReferences
@attr.s
class RepoInfo(object):
    """
    Cloned repository information
    :param type: VCS type
    :param url: repository url
    :param branch: revision branch
    :param commit: revision number
    :param root: clone location path
    """

    type = attr.ib(type=str)
    url = attr.ib(type=str)
    branch = attr.ib(type=str)
    commit = attr.ib(type=str)
    root = attr.ib(type=str)


RType = TypeVar("RType")


GITHUB_APP_USERNAME = "x-access-token"
GITHUB_HOST = "github.com"


def basic_auth_header(username, secret):
    """The HTTP header that carries a username and a secret as HTTP basic auth, as git expects it."""
    raw = b64encode("{}:{}".format(username, secret).encode("utf-8")).decode("utf-8")
    return "AUTHORIZATION: basic {}".format(raw)


def github_header(token):
    """The HTTP header that carries a GitHub installation token (x-access-token), as git expects it."""
    return basic_auth_header(GITHUB_APP_USERNAME, token)


def scrub_token(output, token, username=GITHUB_APP_USERNAME):
    """Remove the token, and every form of the header that carries it, from git output."""
    if not token:
        return output
    binary = isinstance(output, bytes)
    to_text = ensure_binary if binary else ensure_text
    raw = b64encode("{}:{}".format(username, token).encode("utf-8")).decode("utf-8")
    for secret in (basic_auth_header(username, token), raw, token):
        output = output.replace(to_text(secret), to_text("***"))
    return output


class VCS(object, metaclass=abc.ABCMeta):
    """
    Provides overloaded utilities for handling repositories of different types
    """

    # additional environment variables for VCS commands
    COMMAND_ENV = {}

    PATCH_ADDED_FILE_RE = re.compile(r"^--- a/(?P<path>.*)")

    def __init__(self, session, url, location, revision):
        # type: (Session, Text, PathLike, Text) -> ()
        """
        Create a VCS instance for config and url
        :param session: program session
        :param url: repository url
        :param location: (desired) clone location
        :param revision: desired clone revision
        """
        self.session = session
        self.log = self.session.get_logger("{}.{}".format(__name__, type(self).__name__))
        self.url = url
        self.location = Text(location)
        self._revision = revision
        self.log = self.session.get_logger(__name__)

    @property
    def url_with_auth(self):
        """
        The URL as the task gave it, with the user and password it carries (the agent adds none of its own)
        """
        return self.url

    @property
    def url_without_auth(self):
        """
        The URL without any user and password
        """
        return self.add_auth(self.url, reset_auth=True)

    @abc.abstractmethod
    def executable_name(self):
        """
        Name of command executable
        """
        pass

    @abc.abstractmethod
    def main_branch(self):
        """
        Name of default/main branch
        """
        pass

    @abc.abstractmethod
    def checkout_flags(self):
        # type: () -> Sequence[Text]
        """
        Command-line flags for checkout command
        """
        pass

    @abc.abstractmethod
    def patch_base(self):
        # type: () -> Sequence[Text]
        """
        Command and flags for applying patches
        """
        pass

    def patch(self, location, patch_content):
        # type: (PathLike, Text) -> bool
        """
        Apply patch repository at `location`
        """
        self.log.info("applying diff to %s" % location)

        # noinspection PyBroadException
        try:
            for match in filter(None, map(self.PATCH_ADDED_FILE_RE.match, patch_content.splitlines())):
                file_path = None
                # noinspection PyBroadException
                try:
                    file_path = normalize_path(location, match.group("path"))
                    create_file_if_not_exists(file_path)
                except Exception:
                    if file_path:
                        self.log.warning("failed creating file for git diff (%s)" % file_path)
        except Exception:
            pass

        return_code, errors = self.call_with_stdin(patch_content, *self.patch_base, cwd=location)
        if return_code:
            self.log.error("Failed applying diff")
            lines = errors.splitlines()
            if any(l for l in lines if "no such file or directory" in l.lower()):
                self.log.warning(
                    "NOTE: files were not found when applying diff, perhaps you forgot to push your changes?"
                )
            return False
        else:
            self.log.info("successfully applied uncommitted changes")
        return True

    def clone_flags(self):
        """Command-line flags for clone command"""
        return tuple()

    @abc.abstractmethod
    def executable_not_found_error_help(self):
        # type: () -> Text
        """
        Instructions for when executable is not found
        """
        pass

    @staticmethod
    def remote_branch_name(branch):
        # type: (Text) -> Text
        """
        Creates name of remote branch from name of local/ambiguous branch.
        Returns same name by default.
        """
        return branch

    # parse scp-like git ssh URLs, e.g: git@host:user/project.git
    SSH_URL_GIT_SYNTAX = re.compile(
        r"""
        ^
        (?:(?P<user>{regular}*?)@)?
        (?P<host>{regular}*?)
        :
        (?P<path>{regular}.*)?
        $
        """.format(regular=r"[^/@:#]"),
        re.VERBOSE,
    )

    @classmethod
    def replace_ssh_url(cls, url, replacement_scheme="https"):
        # type: (Text) -> Text
        """
        Replace SSH URL with HTTPS URL when applicable
        """

        def get_username(user_, password=None):
            """
            Remove special SSH users hg/git
            """
            return None if user_ and user_.lower() in ["hg", "git"] and not password else user_

        match = cls.SSH_URL_GIT_SYNTAX.match(url)
        if match:
            user, host, path = match.groups()
            return furl().set(scheme=replacement_scheme, username=get_username(user), host=host, path=path).url
        parsed_url = furl(url)
        if parsed_url.scheme == "ssh":
            return parsed_url.set(
                scheme=replacement_scheme,
                username=get_username(parsed_url.username, password=parsed_url.password),
            ).url
        return url

    def clone(self, branch=None):
        # type: (Text) -> None
        """
        Clone repository to destination and checking out `branch`.
        If not in debug mode, filter VCS password from output.
        """
        url = self._remote_url()
        clone_command = ("clone", url, self.location) + self.clone_flags()
        # clone all branches regardless of when we want to later checkout
        # if branch:
        #    clone_command += ("-b", branch)
        if self.session.debug_mode:
            self.call(*clone_command)
            return

        try:
            self._print_output(self._normalize_output(self.get_stderr(*clone_command)))
        except subprocess.CalledProcessError as e:
            # In Python 3, subprocess.CalledProcessError has a `stderr` attribute,
            # but since stderr is redirect to `subprocess.PIPE` it will appear in the usual `output` attribute
            if e.output:
                e.output = self._normalize_output(e.output)
                self._print_output(e.output)
            raise

    _header_token = None
    _header_username = GITHUB_APP_USERNAME

    def _normalize_output(self, result):
        """
        Returns result string without user's password.
        NOTE: ``self.get_stderr``'s result might or might not have the same type as ``e.output`` in case of error.
        """
        string_type = ensure_text if isinstance(result, str) else ensure_binary
        result = result.replace(
            string_type(self.url),
            string_type(furl(self.url).remove(password=True).tostr()),
        )
        return scrub_token(result, self._header_token, self._header_username)

    @staticmethod
    def _print_output(output):
        print(ensure_text(output))

    def checkout(self):
        # type: () -> None
        """
        Checkout repository at specified revision
        """
        self.call("checkout", self._revision, *self.checkout_flags, cwd=self.location)

    @abc.abstractmethod
    def pull(self):
        # type: () -> None
        """
        Pull remote changes for revision
        """
        pass

    def call(self, *argv, **kwargs):
        """
        Execute argv without stdout/stdin.
        Remove stdin so git/hg can't ask for passwords.
        ``kwargs`` can override all arguments passed to subprocess.
        """
        return self._call_subprocess(subprocess.check_call, argv, **kwargs)

    def call_with_stdin(self, input_, *argv, **kwargs):
        # type: (...) -> Tuple[int, str]
        """
        Run command with `input_` as stdin
        """
        input_ = input_.encode("utf-8")
        # always add extra empty line
        # (there is no downside, and it solves empty lines issue at end of patch cause corrupt message)
        input_ += b"\n"
        process = self._call_subprocess(
            subprocess.Popen,
            argv,
            **dict(
                kwargs,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ),
        )
        _, stderr = process.communicate(input_)
        if stderr:
            self.log.warning("%s: %s", self._get_vcs_command(argv), stderr)
        return process.returncode, Text(stderr)

    def get_stderr(self, *argv, **kwargs):
        """
        Execute argv without stdout/stdin in <cwd> and get stderr output.
        Remove stdin so git/hg can't ask for passwords.
        ``kwargs`` can override all arguments passed to subprocess.
        """
        process = self._call_subprocess(subprocess.Popen, argv, **dict(kwargs, stderr=subprocess.PIPE, stdout=None))
        _, stderr = process.communicate()
        code = process.poll()
        if code == COMMAND_SUCCESS:
            return stderr
        with Argv.normalize_exception(censor_password=True):
            raise subprocess.CalledProcessError(returncode=code, cmd=argv, output=stderr)

    def _call_subprocess(self, func, argv, **kwargs):
        # type: (Callable[..., RType], Iterable[Text], dict) ->  RType
        cwd = kwargs.pop("cwd", None)
        cwd = cwd and str(cwd)
        kwargs = dict(
            dict(
                censor_password=True,
                cwd=cwd,
                stdin=DEVNULL,
                stdout=DEVNULL,
                env=self._command_environment(),
            ),
            **kwargs,
        )
        command = self._get_vcs_command(argv)
        self.log.debug("Running: %s", list(command))
        return command.call_subprocess(func, **kwargs)

    def _command_environment(self):
        """The environment of a command: the process's, with the values this class needs on top."""
        return dict(environ, **self.COMMAND_ENV)

    def _remote_url(self):
        """The address a clone or fetch is made from."""
        return self.url_with_auth

    def _get_vcs_command(self, argv):
        # type: (Iterable[PathLike]) -> Argv
        return Argv(self.executable_name, *argv)

    @staticmethod
    def add_auth(url, reset_auth=False):
        """
        The URL, or with reset_auth the URL without the user and password it carries. Never adds a credential: the
        agent has none of its own (see resolve_git_clone).
        """
        try:
            parsed_url = furl(url)
        except ValueError:
            return url
        if reset_auth and parsed_url.scheme not in ["", "ssh"] and not (parsed_url.scheme or "").startswith("git"):
            parsed_url.set(username=None, password=None)
            return parsed_url.url
        return url

    @abc.abstractmethod
    def info_commands(self):
        # type: () -> Mapping[Text, Argv]
        """
        `   Mapping from `RepoInfo` attribute name (except `type`) to command which acquires it
        """
        pass

    def get_repository_copy_info(self, path):
        """
        Get `RepoInfo` instance from copy of clone in `path`
        """
        path = Text(path)
        commands_result = {
            name: command.get_output(cwd=path)
            # name: subprocess.check_output(command.split(), cwd=path).decode().strip()
            for name, command in self.info_commands.items()
        }
        return RepoInfo(type=self.executable_name, **commands_result)


# What a git command of the agent must not take from the machine it runs on: an SSH agent, ssh itself, askpass
# helpers and every git configuration the host or the user set, since each can hand the host's own credentials to
# the clone. The agent's commands run with the values of Git.COMMAND_ENV instead (see Git._command_environment).
_GIT_ENV_BLOCKED = frozenset(
    {
        "SSH_AUTH_SOCK",
        "SSH_ASKPASS",
        "SSH_ASKPASS_REQUIRE",
        "GIT_ASKPASS",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
        "GIT_CONFIG",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
    }
)
_GIT_ENV_BLOCKED_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
# ssh is never used for a job's repository: an address in ssh form is refused (see resolve_git_clone), and a
# submodule or redirect that asks for it fails here
_REFUSING_SSH_COMMAND = "false"

_agent_gitconfig_file = None


def agent_gitconfig():
    """
    The agent's own global git configuration, a file of its own (removed at exit) that stands in for the host's
    ~/.gitconfig, ~/.config/git/config and the system configuration. It holds what the agent writes (safe.directory).
    """
    global _agent_gitconfig_file
    if _agent_gitconfig_file is None or not os.path.exists(_agent_gitconfig_file):
        handle, _agent_gitconfig_file = tempfile.mkstemp(prefix="cairnml_gitconfig_")
        os.close(handle)
        atexit.register(safe_remove_file, _agent_gitconfig_file)
    return _agent_gitconfig_file


class Git(VCS):
    executable_name = "git"
    main_branch = ("master", "main")
    checkout_flags = ("--force",)
    COMMAND_ENV = {
        # do not prompt for password
        "GIT_TERMINAL_PROMPT": "0",
        # no host or system git configuration (credential helpers, url rewrites, an ssh command): the agent's own
        # file stands in for it, see agent_gitconfig(); this value is set per instance
        "GIT_CONFIG_NOSYSTEM": "1",
        # never ssh: the host's keys are not the job's credential
        "GIT_SSH_COMMAND": _REFUSING_SSH_COMMAND,
    }

    def __init__(self, *args, **kwargs):
        super(Git, self).__init__(*args, **kwargs)
        # this instance's own environment, so a token given to it is not shared with other repositories
        self.COMMAND_ENV = dict(self.COMMAND_ENV, GIT_CONFIG_GLOBAL=agent_gitconfig())

        self._use_ask_pass = (
            False if not self.session.config.get("agent.enable_git_ask_pass", True) else sys.platform == "linux"
        )

        self._use_ms_entra_token = ENV_AGENT_GIT_USE_MS_ENTRA_TOKEN.get() or self.session.config.get(
            "agent.git_use_ms_entra_token", False
        )

        self._use_azure_pat = ENV_AGENT_GIT_USE_AZURE_PAT.get() or self.session.config.get(
            "agent.git_use_azure_pat", False
        )

        if self._use_ms_entra_token and self._use_azure_pat:
            print("WARNING: Git MS Extra Token and Azure PAT support are both turned on, using MS Extra Token")
            self._use_azure_pat = None

        try:
            self.call("config", "--global", "--replace-all", "safe.directory", "*", cwd=self.location)
        except:  # noqa
            pass

    def _command_environment(self):
        """The process's environment without what could carry the host's credentials, plus COMMAND_ENV."""
        env = {
            name: value
            for name, value in environ.items()
            if name not in _GIT_ENV_BLOCKED and not name.startswith(_GIT_ENV_BLOCKED_PREFIXES)
        }
        env.update(self.COMMAND_ENV)
        return env

    def _remote_url(self):
        # if we are on linux no need for the full auth url because we use GIT_ASKPASS
        return (
            self.url_without_auth
            if self._use_ask_pass or self._use_ms_entra_token or self._use_azure_pat or self._header_token
            else self.url_with_auth
        )

    @staticmethod
    def remote_branch_name(branch):
        return ["origin/{}".format(b) for b in ([branch] if isinstance(branch, str) else branch)]

    def clone_flags(self):
        return ("--recursive", "--verbose" if ENV_GIT_CLONE_VERBOSE.get() else "--quiet")

    def executable_not_found_error_help(self):
        return 'Cannot find "{}" executable. {}'.format(
            self.executable_name,
            select_for_platform(
                linux="You can install it by running: sudo apt-get install {}".format(self.executable_name),
                windows="You can download it here: {}".format("https://gitforwindows.org/"),
            ),
        )

    def pull(self):
        # a cached clone made by an earlier agent can hold credentials in its origin address: use this run's address
        try:
            self.call("remote", "set-url", "origin", self._remote_url(), cwd=self.location)
        except subprocess.CalledProcessError:
            pass
        fetch_args = self.session.config.get("agent.git_fetch_args")
        if not isinstance(fetch_args, (list, tuple)):
            self.log.warning(
                "agent.git_fetch_args must be a list, got %s; ignoring",
                type(fetch_args).__name__,
            )
            fetch_args = []
        self.call("fetch", *fetch_args, cwd=self.location)

    def use_header_token(self, token, username=GITHUB_APP_USERNAME, url=None):
        """
        Authenticate the git commands that reach the repository's own host with a token sent as HTTP basic auth in
        an HTTP header in the environment (GIT_CONFIG_COUNT). The header is scoped to the origin of the clone
        address (http.<origin>/.extraheader), so a submodule or a redirect on another host never receives it. The
        token is never in the URL, on the command line or in a file, and no askpass helper is used. A GitHub App
        token uses the username x-access-token; a stored Git credential uses its own.
        """
        address = furl(url or self.url)
        if address.scheme not in ("http", "https") or not address.host:
            raise ValueError("a token can only be sent to an http(s) address, not to {}".format(address.scheme))
        self._header_token = token
        self._header_username = username
        self.COMMAND_ENV.update(
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0="http.{}/.extraheader".format(address.origin),
            GIT_CONFIG_VALUE_0=basic_auth_header(username, token),
        )

    def _git_pass_auth_wrapper(self, func, *args, **kwargs):
        if self._header_token:
            return func(*args, **kwargs)
        try:
            url_with_auth = furl(self.url_with_auth)
            password = url_with_auth.password if url_with_auth else None
            username = url_with_auth.username if url_with_auth else None
        except:  # noqa
            password = None
            username = None

        if self._use_ms_entra_token or self._use_azure_pat:
            if not password:
                print("WARNING: cannot use MS Entra Token or Azure PAT since no password was found, skipping")
            elif self._use_ms_entra_token:
                return func("-c", f'http.extraheader="Authorization: Bearer {password}"', *args, **kwargs)
            else:  # self._use_azure_pat
                b64_pat = b64encode(f":{password}".encode("utf-8")).decode("utf-8")
                self.COMMAND_ENV["AZURE_PAT_HEADER_VALUE"] = f"Authorization: Basic {b64_pat}"
                # call git command
                try:
                    ret = func("--config-env=http.extraheader=AZURE_PAT_HEADER_VALUE", *args, **kwargs)
                finally:
                    # delete temp password file
                    self.COMMAND_ENV.pop("AZURE_PAT_HEADER_VALUE", None)
                return ret

        # if this is not linux or we do not have a password, just run as is
        if not self._use_ask_pass or not password or not username:
            return func(*args, **kwargs)

        # create the askpass script; it holds no credentials, it echoes them from the environment
        fp, pass_file = tempfile.mkstemp(prefix="cairnml_git_", suffix=".sh")
        os.close(fp)
        with open(pass_file, "wt") as f:
            # get first letter only (username / password are the argument options)
            # then echo the correct information
            f.writelines(
                [
                    "#!/bin/bash\n",
                    'c="$1"\n',
                    'c="${c%"${c#?}"}"\n',
                    'if [ "$c" == "u" ] || [ "$c" == "U" ]; then echo "$CAIRNML_GIT_USERNAME"; else echo "$CAIRNML_GIT_PASSWORD"; fi\n',
                ]
            )
        # mark executable
        st = os.stat(pass_file)
        os.chmod(pass_file, st.st_mode | stat.S_IEXEC)
        # let GIT use it, the credentials reach the script through the command environment
        self.COMMAND_ENV["CAIRNML_GIT_USERNAME"] = username
        self.COMMAND_ENV["CAIRNML_GIT_PASSWORD"] = password
        self.COMMAND_ENV["GIT_ASKPASS"] = pass_file
        # call git command
        try:
            ret = func(*args, **kwargs)
        finally:
            # remove the credentials and delete the askpass script
            self.COMMAND_ENV.pop("GIT_ASKPASS", None)
            self.COMMAND_ENV.pop("CAIRNML_GIT_USERNAME", None)
            self.COMMAND_ENV.pop("CAIRNML_GIT_PASSWORD", None)
            safe_remove_file(pass_file)

        return ret

    def get_stderr(self, *argv, **kwargs):
        """
        Wrapper with git password authentication
        """
        return self._git_pass_auth_wrapper(super(Git, self).get_stderr, *argv, **kwargs)

    def call_with_stdin(self, *argv, **kwargs):
        """
        Wrapper with git password authentication
        """
        return self._git_pass_auth_wrapper(super(Git, self).call_with_stdin, *argv, **kwargs)

    def call(self, *argv, **kwargs):
        """
        Wrapper with git password authentication
        """
        return self._git_pass_auth_wrapper(super(Git, self).call, *argv, **kwargs)

    def checkout(self):  # type: () -> None
        """
        Checkout repository at specified revision
        """
        revisions = [self._revision] if isinstance(self._revision, str) else self._revision
        for i, revision in enumerate(revisions):
            try:
                self.call("checkout", revision, *self.checkout_flags, cwd=self.location)
                break
            except subprocess.CalledProcessError:
                if i == len(revisions) - 1:
                    raise

        try:
            self.call("submodule", "update", "--recursive", cwd=self.location)
        except:  # noqa
            pass

    info_commands = dict(
        url=Argv(executable_name, "ls-remote", "--get-url", "origin"),
        branch=Argv(executable_name, "rev-parse", "--abbrev-ref", "HEAD"),
        commit=Argv(executable_name, "rev-parse", "HEAD"),
        root=Argv(executable_name, "rev-parse", "--show-toplevel"),
    )

    patch_base = (
        "apply",
        "--unidiff-zero",
    )


class Hg(VCS):
    executable_name = "hg"
    main_branch = "default"
    checkout_flags = ("--clean",)
    patch_base = ("import", "--no-commit")

    def executable_not_found_error_help(self):
        return 'Cannot find "{}" executable. {}'.format(
            self.executable_name,
            select_for_platform(
                linux="You can install it by running: sudo apt-get install {}".format(self.executable_name),
                windows="You can download it here: {}".format("https://www.mercurial-scm.org/wiki/Download"),
            ),
        )

    def pull(self):
        self.call("pull", self.url_with_auth, cwd=self.location, *(("-r", self._revision) if self._revision else ()))

    info_commands = dict(
        url=Argv(executable_name, "paths", "--verbose"),
        branch=Argv(executable_name, "--debug", "id", "-b"),
        commit=Argv(executable_name, "--debug", "id", "-i"),
        root=Argv(executable_name, "root"),
    )


def request_git_token(session, task_id):
    """
    The server's answer to the Git credential request for the task's repository (a CallResult), or None when the
    server refuses it. The answer's clone url (apiserver/bll/repository_access.py:279) is read from its data, not from
    the typed response: the definitions before 2.41 do not declare it, and the typed response would drop it.
    """
    try:
        result = session.send(session.current(devices_api.GitTokenRequest)(task=task_id))
    except Exception as ex:
        # a server that cannot be reached, or that does not serve the call, gives no answer (the job then fails)
        print("The credential request for the repository failed: {}".format(ex))
        return None
    if result is None or not result.ok():
        return None
    return result


# The settings and environment variables that gave the agent Git credentials of its own, or made it use the host's SSH
# setup. They are gone (cairnml#194): a job's repository is read with the token the server issues for it, or anonymously.
# A configuration that still has them is told so once (see removed_credential_settings); none of them has an effect.
REMOVED_SETTINGS = (
    "agent.git_user",
    "agent.git_pass",
    "agent.git_host",
    "agent.git_host_match_prefix",
    "agent.git",
    "agent.force_git_ssh_protocol",
    "agent.force_git_ssh_port",
    "agent.force_git_ssh_user",
    "agent.disable_ssh_mount",
    "agent.translate_ssh",
    "agent.translate_ssl_replacement_scheme",
)
REMOVED_ENVIRONMENT_VARIABLES = (
    "CAIRNML_AGENT_GIT_USER",
    "CAIRNML_AGENT_GIT_PASS",
    "CAIRNML_AGENT_GIT_HOST",
    "CAIRNML_AGENT_DISABLE_SSH_MOUNT",
    "CAIRNML_AGENT_SSH_URL_REPLACEMENT_SCHEME",
)


def removed_credential_settings(config, environment=None):
    """The names of the removed settings and environment variables that are set to something (empty and false values
    are not a credential and are not named). Values are never returned."""
    environment = environ if environment is None else environment
    named = [name for name in REMOVED_SETTINGS if config.get(name, None)]
    return named + [name for name in REMOVED_ENVIRONMENT_VARIABLES if environment.get(name)]


class RepositoryAccessError(Exception):
    """The job's repository cannot be read with what the agent may use; the message says what to do about it."""


ASSIGN_OR_INSTALL = (
    "assign a credential to it under Repositories & Datasets in the web app, or install the GitHub App on it"
)


def _address_without_password(repository_url):
    """The address for a message: without a password it may carry."""
    try:
        return furl(repository_url).remove(password=True).tostr()
    except ValueError:
        return repository_url


def repository_access_message(repository_url, reason):
    """The message of a repository that cannot be read: the repository, why, and the two remedies."""
    return "The repository {} could not be read: {}. If it is private, {}.".format(
        _address_without_password(repository_url) if repository_url else "(none)", reason, ASSIGN_OR_INSTALL
    )


def git_credential_from_answer(res):
    """
    (username, secret) from a successful answer. A public repository is answered with a null token: it has no
    credential, so None is returned and the repository is cloned without an authorization header.
    """
    response = res.response
    token = response.token if response else None
    if not token:
        return None
    return (response.username or GITHUB_APP_USERNAME), token


def fetch_git_credential(session, task_id):
    """
    The read-only Git credential for the task's repository, from the server: a GitHub App token on GitHub, or
    the credential the administrator stored for the repository's host (and path prefix) on any other host.
    Returns (username, secret), or None when the server gives no credential (a public repository, or no answer).
    """
    res = request_git_token(session, task_id)
    if res is None:
        return None
    return git_credential_from_answer(res)


def fetch_git_token(session, task_id):
    """
    The read-only token for the task's repository, from the server (see fetch_git_credential). None when the
    server has no credential for it.
    """
    credential = fetch_git_credential(session, task_id)
    return credential[1] if credential else None


SCP_LIKE_URL_RE = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>[^/].*)$")


def is_github_repository(repository_url):
    """
    Whether the repository address points at GitHub, in any of its forms: git@github.com:owner/name.git,
    ssh://git@github.com/owner/name.git, https:// or http:// URLs, github.com/owner/name, or owner/name.
    """
    url = (repository_url or "").strip()
    if not url:
        return False
    if "://" in url:
        return (furl(url).host or "").lower() == GITHUB_HOST
    scp_like = SCP_LIKE_URL_RE.match(url)
    if scp_like:
        return scp_like.group("host").lower() == GITHUB_HOST
    first, _, rest = url.partition("/")
    if first.lower() == GITHUB_HOST:
        return bool(rest.strip("/"))
    # owner/name: a GitHub owner has no dot in its name, a host always has one
    return bool(first) and "." not in first and bool(rest.strip("/")) and "/" not in rest.strip("/")


def wants_stored_credential(repository_url):
    """
    Whether the agent asks the server for a Git credential for this repository URL. GitHub is always asked, in
    any form of address (the server answers with the GitHub App or the stored credential). Any other host is
    asked only over HTTPS, so a credential is never sent in clear text; over other schemes (ssh, plain http,
    git://) the repository is read without a credential, and an ssh address is refused (see resolve_git_clone).
    """
    if is_github_repository(repository_url):
        return True
    parsed = furl(repository_url or "")
    host = (parsed.host or "").lower()
    return parsed.scheme == "https" and bool(host)


def _is_http_address(repository_url):
    try:
        return (furl(repository_url or "").scheme or "").lower() in ("http", "https")
    except ValueError:
        return False


def is_ssh_address(repository_url):
    """Whether the address is in ssh form: ssh://..., or git@host:owner/name (scp-like)."""
    url = (repository_url or "").strip()
    if "://" in url:
        return (furl(url).scheme or "").lower() in ("ssh", "git+ssh")
    return bool(SCP_LIKE_URL_RE.match(url)) and not os.path.isabs(url)


def github_https_address(repository_url):
    """The https address of a GitHub repository given in any form (see is_github_repository)."""
    if not is_ssh_address(repository_url):
        return repository_url
    https_address = furl(VCS.replace_ssh_url(repository_url, replacement_scheme="https"))
    # an ssh port (ssh://git@github.com:22/...) is not an https port
    https_address.set(port=None)
    return https_address.url


def resolve_git_clone(session, repository_url, task_id):
    """
    How the agent clones the task's repository, as (clone_url, credential). The only credential a repository is read
    with is the token the server issues for it, or none (a public repository): the host's SSH keys and git setup and
    the agent's own configuration are never used.
    - clone_url is the address to clone from: the one the server gives, else the task's address with an ssh-form
      GitHub address made https; None when the task's address is used as it is.
    - credential is (username, secret) or None.
    An answer with a token means "use it", an answer without one means the repository is public or has no credential
    (the clone is then anonymous), and no answer or a refusal fails the job: nothing on this machine stands in for it.
    :raises: RepositoryAccessError for an ssh address of another host than GitHub, and when the server gives no answer
    """
    url = (repository_url or "").strip()
    clone_url = None
    if is_ssh_address(url):
        if not is_github_repository(url):
            raise RepositoryAccessError(
                "The repository {} is given as an ssh address, and the agent never uses ssh. Use its https address "
                "instead (https://<host>/<owner>/<name>.git) and make sure the server has a credential for it if "
                "it is private.".format(_address_without_password(url))
            )
        clone_url = github_https_address(url)
    if not task_id or not wants_stored_credential(clone_url or url):
        return clone_url, None
    res = request_git_token(session, task_id)
    if res is None:
        raise RepositoryAccessError(
            repository_access_message(url, "the server refused or did not answer the request for its credential")
        )
    credential = git_credential_from_answer(res)
    answer_url = (res.response_data or {}).get("url")
    return (answer_url or clone_url), credential


def select_git_credential(session, repository_url, task_id):
    """
    The credential the agent uses to clone the task's repository, as (username, secret), or None when the repository
    is cloned without one (see resolve_git_clone).
    """
    return resolve_git_clone(session, repository_url, task_id)[1]


def clone_repository_cached(session, execution, destination, task_id=None):
    # type: (Session, ExecutionInfo, Path, Optional[str]) -> Tuple[VCS, RepoInfo]
    """
    Clone a remote repository.
    :param execution: execution info
    :param destination: directory to clone to (in which a directory for the repository will be created)
    :param session: program session
    :param task_id: the task being run; its repository is cloned with the Git credential the server gives for it
    :return: repository information
    :raises: CommandFailedError if git/hg is not installed
    """
    # mock lock
    repo_lock = Lock()
    repo_lock_timeout_sec = 300
    repo_url = execution.repository or ""  # type: str
    parsed_url = furl(repo_url)
    no_password_url = parsed_url.copy().remove(password=True).url

    clone_folder_name = Path(str(furl(repo_url).path)).name  # type: str
    clone_folder = Path(destination) / clone_folder_name

    standalone_mode = session.config.get("agent.standalone_mode", False)
    if standalone_mode:
        cached_repo_path = clone_folder
    else:
        vcs_cache_path = Path(session.config["agent.vcs_cache.path"]).expanduser()
        repo_hash = md5(ensure_binary(repo_url)).hexdigest()
        # create lock
        repo_lock = FileLock(filename=(vcs_cache_path / "{}.lock".format(repo_hash)).as_posix())
        # noinspection PyBroadException
        try:
            print("INFO: acquiring [timeout={}s] lock for repo cache folder".format(repo_lock_timeout_sec))
            repo_lock.acquire(timeout=repo_lock_timeout_sec)
        except BaseException:
            print(
                'Could not lock cache folder "{}" (timeout {} sec), using temp vcs cache.'.format(
                    clone_folder_name, repo_lock_timeout_sec
                )
            )
            repo_hash = "{}_{}".format(repo_hash, str(random()).replace(".", ""))
            # use mock lock for the context
            repo_lock = Lock()
        # select vcs cache folder
        cached_repo_path = vcs_cache_path / "{}.{}".format(clone_folder_name, repo_hash) / clone_folder_name

    with repo_lock:
        vcs = VcsFactory.create(session, execution_info=execution, location=cached_repo_path)
        if not find_executable(vcs.executable_name):
            raise CommandFailedError(vcs.executable_not_found_error_help())
        clone_url = None
        credential = None
        if isinstance(vcs, Git):
            clone_url, credential = resolve_git_clone(session, repo_url, task_id)
            if clone_url:
                vcs.url = clone_url
            if credential:
                vcs.use_header_token(credential[1], username=credential[0], url=vcs.url)

        if not standalone_mode:
            try:
                if session.config["agent.vcs_cache.enabled"] and cached_repo_path.exists():
                    print('Using cached repository in "{}"'.format(cached_repo_path))

                else:
                    print("cloning: {}".format(clone_url or no_password_url))
                    rm_tree(cached_repo_path)
                    # We clone the entire repository, not a specific branch
                    vcs.clone()  # branch=execution.branch)

                print("pulling git")
                try:
                    vcs.pull()
                except Exception as ex:
                    print("git pull failed: {}".format(ex))
                    if session.config.get("agent.vcs_cache.enabled", False) and session.config.get(
                        "agent.vcs_cache.clone_on_pull_fail", False
                    ):
                        print("pulling git failed, re-cloning: {}".format(no_password_url))
                        rm_tree(cached_repo_path)
                        vcs.clone()
                    else:
                        raise ex
                print("pulling git completed")
            except subprocess.CalledProcessError as ex:
                if isinstance(vcs, Git) and credential is None and _is_http_address(vcs.url):
                    raise RepositoryAccessError(
                        repository_access_message(
                            repo_url, "the server issued no token for it and it cannot be read without one"
                        )
                    ) from ex
                raise

            rm_tree(destination)
            shutil.copytree(
                Text(cached_repo_path),
                Text(clone_folder),
                symlinks=select_for_platform(linux=True, windows=False),
                ignore_dangling_symlinks=True,
            )
            if not clone_folder.is_dir():
                raise CommandFailedError(
                    "copying of repository failed: from {} to {}".format(cached_repo_path, clone_folder)
                )

            # checkout in the newly copy destination
            vcs.location = Text(clone_folder)
            vcs.checkout()

    repo_info = vcs.get_repository_copy_info(clone_folder)

    # make sure we have no user/pass in the returned repository structure
    repo_info = attr.evolve(repo_info, url=no_password_url)

    return vcs, repo_info


def fix_package_import_diff_patch(entry_script_file):
    # noinspection PyBroadException
    try:
        with open(entry_script_file, "rt") as f:
            lines = f.readlines()
    except Exception:
        return
    # make sure we are the first import (i.e. we patched the source code)
    if len(lines or []) < 2 or not lines[0].strip().startswith("from cairnml ") or "Task.init" not in lines[1]:
        return

    original_lines = lines
    # skip over the first two lines, they are ours
    # then skip over empty or comment lines
    lines = [
        (i, line.split("#", 1)[0].rstrip())
        for i, line in enumerate(lines)
        if i >= 2 and line.strip("\r\n\t ") and not line.strip().startswith("#")
    ]

    # remove triple quotes ' """ '
    nested_c = -1
    skip_lines = []
    for i, line_pair in enumerate(lines):
        for _ in line_pair[1].split('"""')[1:]:
            if nested_c >= 0:
                skip_lines.extend(list(range(nested_c, i + 1)))
                nested_c = -1
            else:
                nested_c = i
    # now select all the
    lines = [pair for i, pair in enumerate(lines) if i not in skip_lines]

    from_future = re.compile(r"^from[\s]*__future__[\s]*")
    import_future = re.compile(r"^import[\s]*__future__[\s]*")
    # test if we have __future__ import
    found_index = -1
    for a_i, (_, a_line) in enumerate(lines):
        if found_index >= a_i:
            continue
        if from_future.match(a_line) or import_future.match(a_line):
            found_index = a_i
            # check the last import block
            i, line = lines[found_index]
            # wither we have \\ character at the end of the line or the line is indented
            parenthesized_lines = "(" in line and ")" not in line
            while line.endswith("\\") or parenthesized_lines:
                found_index += 1
                i, line = lines[found_index]
                if ")" in line:
                    break

        else:
            break

    # no imports found
    if found_index < 0:
        return

    # now we need to move back the patched two lines
    entry_line = lines[found_index][0]
    new_lines = original_lines[2 : entry_line + 1] + original_lines[0:2] + original_lines[entry_line + 1 :]
    # noinspection PyBroadException
    try:
        with open(entry_script_file, "wt") as f:
            f.writelines(new_lines)
    except Exception:
        return


def _locate_future_import(lines):
    # type: (list[str]) -> int
    """
    :param lines: string lines of a python file
    :return: line index of the last __future_ import. return -1 if no __future__ was found
    """
    # skip over the first two lines, they are ours
    # then skip over empty or comment lines
    lines = [
        (i, line.split("#", 1)[0].rstrip())
        for i, line in enumerate(lines)
        if line.strip("\r\n\t ") and not line.strip().startswith("#")
    ]

    # remove triple quotes ' """ '
    nested_c = -1
    skip_lines = []
    for i, line_pair in enumerate(lines):
        for _ in line_pair[1].split('"""')[1:]:
            if nested_c >= 0:
                skip_lines.extend(list(range(nested_c, i + 1)))
                nested_c = -1
            else:
                nested_c = i
    # now select all the
    lines = [pair for i, pair in enumerate(lines) if i not in skip_lines]

    from_future = re.compile(r"^from[\s]*__future__[\s]*")
    import_future = re.compile(r"^import[\s]*__future__[\s]*")
    # test if we have __future__ import
    found_index = -1
    for a_i, (_, a_line) in enumerate(lines):
        if found_index >= a_i:
            continue
        if from_future.match(a_line) or import_future.match(a_line):
            found_index = a_i
            # check the last import block
            i, line = lines[found_index]
            # wither we have \\ character at the end of the line or the line is indented
            parenthesized_lines = "(" in line and ")" not in line
            while line.endswith("\\") or parenthesized_lines:
                found_index += 1
                i, line = lines[found_index]
                if ")" in line:
                    break

        else:
            break

    return found_index if found_index < 0 else lines[found_index][0]


def patch_add_task_init_call(local_filename):
    if not local_filename or not Path(local_filename).is_file() or not str(local_filename).lower().endswith(".py"):
        return

    idx_a = 0
    # find the right entry for the patch if we have a local file (basically after __future__
    try:
        with open(local_filename, "rt") as f:
            lines = f.readlines()
    except Exception as ex:
        print("Failed patching entry point file {}: {}".format(local_filename, ex))
        return

    future_found = _locate_future_import(lines)
    if future_found >= 0:
        idx_a = future_found + 1

    # check if we have not already patched it, no need to add another one
    if (
        len(lines or []) >= idx_a + 2
        and lines[idx_a].strip().startswith("from cairnml ")
        and "Task.init" in lines[idx_a + 1]
    ):
        print("File {} already patched with Task.init()".format(local_filename))
        return

    patch = [
        "from cairnml import Task\n",
        '(__name__ != "__main__") or Task.init()\n',
    ]
    lines = lines[:idx_a] + patch + lines[idx_a:]
    # noinspection PyBroadException
    try:
        with open(local_filename, "wt") as f:
            f.writelines(lines)
    except Exception as ex:
        print("Failed patching entry point file {}: {}".format(local_filename, ex))
        return

    print("Force cairnml Task.init patch adding to entry point script: {}".format(local_filename))

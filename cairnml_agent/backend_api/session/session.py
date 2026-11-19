"""The agent's session: the SDK's session with what only the agent does.

The SDK makes the session, the token handling, the HTTP layer and the retries. The agent differs in these points, and
only these are kept here:
- its configuration is the agent's own, and its host has no default (the agent refuses to guess one);
- it refuses to start without an enrolment, and says so in its own words;
- its device key is the agent's key, in the agent's folder;
- the start-up sign-in tries to connect a few times, unless the environment switches that off (the SDK's hook);
- the feature checks compare with the API level of the server, not of the SDK;
- its own identity headers, and its own time-outs for requests;
- it does not load the server's vaults at start-up;
- once started, it keeps going over dropped connections instead of raising.
"""

import logging
import time
from random import SystemRandom
from typing import ClassVar

import requests
from cairnml.backend_api.services import UnsupportedApiVersion, resolve
from cairnml.backend_api.session.session import LoginError
from cairnml.backend_api.session.session import Session as SdkSession

from ...errors import APIError
from ...idp import device as agent_device
from ...version import __version__
from ..config import load
from .defs import (
    ENV_AUTH_TOKEN,
    ENV_HOST,
    ENV_INITIAL_CONNECT_RETRY_OVERRIDE,
    ENV_JOB_TOKEN,
    ENV_JOB_TOKEN_FILE,
    ENV_NO_DEFAULT_SERVER,
    ENV_URLLIB3_CONNECT_LEVEL,
)

sys_random = SystemRandom()

# the connect retries of the start-up sign-in, unless CAIRNML_AGENT_INITIAL_CONNECT_RETRY_OVERRIDE gives another number
INITIAL_CONNECT_RETRIES = 4


class Session(SdkSession):
    """The agent's session. Its constructor takes the arguments of the agent's commands."""

    # the agent has no offline mode: the SDK's switch would stop it from connecting, so it does not apply here
    _offline_mode = False
    _CLIENT_HEADER = "X-CairnML-Agent"
    _client: ClassVar[list] = [("api", __version__)]
    # the agent's request time-outs (the SDK's are longer)
    _session_timeout = (10.0, 30.0)
    _write_session_timeout = (30.0, 30.0)

    def __init__(
        self,
        worker=None,
        host=None,
        logger=None,
        verbose=None,
        initialize_logging=True,
        config=None,
        http_retries_config=None,
        custom_default_config_paths=None,
        **kwargs,
    ):
        self._command = kwargs.get("command")
        if config is None:
            config = load(*(custom_default_config_paths or ()))
            if initialize_logging:
                config.initialize_logging(debug=kwargs.get("debug", False))
        self._check_enrolled(config, host)

        # after the start-up sign-in, a dropped connection is retried, not raised
        self._propagate_exceptions_on_send = True
        super().__init__(
            worker=worker,
            host=host,
            logger=logger,
            verbose=verbose,
            config=config,
            http_retries_config=http_retries_config,
            device_key_path=str(agent_device.DEVICE_KEY_PATH),
            **kwargs,
        )
        self._propagate_exceptions_on_send = False

    @staticmethod
    def _check_enrolled(config, host):
        """Refuses to start without a token, an enrolment or a job token, and without a host, with the agent's words."""
        token = ENV_AUTH_TOKEN.get(value_cb=_announce_token)
        if not (token or config.get("api.idp.device_id", None) or ENV_JOB_TOKEN.get()):
            raise ValueError("This machine is not enrolled: run cairnml-agent init, or set CAIRNML_AUTH_TOKEN.")
        if not (host or Session.get_api_server_host(config=config)):
            raise ValueError(
                "Could not find host server definition "
                "(missing `~/.cairnml/agent/cairnml-agent.conf` or Environment CAIRNML_API_HOST)\n"
                "To get started with CairnML: setup your own `cairnml-server`, "
                "and run `cairnml-agent init`"
            )

    @classmethod
    def get_api_server_host(cls, config=None):
        """The agent's host: the configured server, or the environment's. There is no default server unless the
        environment allows one."""
        if not config:
            return None
        default = config.get("api.api_server", None) or config.get("api.host", None)
        if not ENV_NO_DEFAULT_SERVER.get():
            default = default or cls.default_host
        return ENV_HOST.get(default=default)

    @classmethod
    def check_min_api_version(cls, min_api_version):
        """The agent's feature checks compare with the API level of the server it talks to."""
        return cls.check_min_api_server_version(min_api_version)

    @classmethod
    def serves(cls, service, action):
        """True when the server serves the action: a definition of it is at or below the level the requests go out at
        (see request_version)."""
        try:
            cls.request_version(service, action)
        except UnsupportedApiVersion:
            return False
        return True

    @classmethod
    def definition(cls, service, action):
        """The generated request and response classes of the definition the action is sent with: the newest definition
        at or below the level the requests go out at."""
        return resolve(service, action, cls.request_version(service, action))

    def send_api(self, request):
        """Send a generated request and return its response object; a refused call raises APIError."""
        result = self.send(request)
        if not result.ok():
            raise APIError(result)
        if not result.response:
            raise APIError(result, extra_info="Invalid response")
        return result.response

    @classmethod
    def current(cls, request_cls):
        """The generated request class to build a call with: the definition of the request's action that the requests
        go out with (see request_version), the newest one of the generated module when the server is that new, an
        older one for an older server. Build the call as `session.send_api(session.current(tasks_api.GetAllRequest)(...))`
        so the request names its generated class and the version comes from the level."""
        service, action = request_cls._service, request_cls._action
        current_cls, _ = resolve(service, action, cls.request_version(service, action))
        return current_cls

    @classmethod
    def declares(cls, service, action, field):
        """True when the definition the action is sent with declares the field. A field a definition added is only
        there from that definition on, so a call that sends it needs a server that serves the definition."""
        if not cls.serves(service, action):
            return False
        request_cls, _ = cls.definition(service, action)
        return field in request_cls._schema.get("properties", {})

    @property
    def _startup_connect_retries(self):
        """The connect retries of the start-up sign-in. The switch is on by default, except for the execute command."""
        kwargs = {"default": False} if self._command == "execute" else {}
        if not ENV_INITIAL_CONNECT_RETRY_OVERRIDE.get(**kwargs):
            return None
        connect_retries = INITIAL_CONNECT_RETRIES
        try:
            value = ENV_INITIAL_CONNECT_RETRY_OVERRIDE.get(converter=str)
            if not isinstance(value, bool):
                connect_retries = abs(int(value))
        except ValueError:
            pass
        return connect_retries

    def _sign_in_at_startup(self):
        """The start-up sign-in logs urllib3's connection messages at the level the environment asks for."""
        urllib3_logger = logging.getLogger("urllib3")
        original_level = urllib3_logger.level
        target = (ENV_URLLIB3_CONNECT_LEVEL.get() or "").strip()
        if target:
            level = int(target) if target.isdigit() else logging.getLevelName(target)
            if not isinstance(level, int):
                print(f"Failed setting initial urllib3 log level: {level}")
            elif level < original_level:
                urllib3_logger.setLevel(level)
        try:
            super()._sign_in_at_startup()
        finally:
            urllib3_logger.setLevel(original_level)

    def _do_refresh_token(self, old_token, exp=None):
        if not (old_token or self.device_id or ENV_JOB_TOKEN.get() or ENV_JOB_TOKEN_FILE.get() or ENV_AUTH_TOKEN.get()):
            raise LoginError("This machine has no device key or token: run cairnml-agent init to enrol it")
        return super()._do_refresh_token(old_token, exp=exp)

    def _exchange_device_assertion(self):
        try:
            return super()._exchange_device_assertion()
        except FileNotFoundError:
            raise LoginError("This machine has no device key: run cairnml-agent init to enrol it") from None

    def _load_vaults(self):
        """The agent does not load the server's vaults at start-up."""
        return

    def _send_request(self, *args, **kwargs):
        """Once started, a request that fails at the connection is sent again, after a short pause, until it is
        answered."""
        while True:
            try:
                return super()._send_request(*args, **kwargs)
            except requests.RequestException as ex:
                if self._propagate_exceptions_on_send:
                    raise
                sleep_time = sys_random.uniform(2.0, 3.0)
                self._logger.error(
                    f"{type(ex).__name__} exception sending a request: {ex} (retrying in {sleep_time:.1f}sec)"
                )
                time.sleep(sleep_time)


def _announce_token(key, value):
    print(f"Using environment access token {key}=********")

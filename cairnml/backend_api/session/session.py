from __future__ import print_function
import json as json_lib
import logging
import sys
import types
import weakref
from io import IOBase
from socket import gethostname
from time import sleep

import jwt
import requests
from requests.exceptions import (
    ChunkedEncodingError,
    ContentDecodingError,
    StreamConsumedError,
)
from six.moves.urllib.parse import urlparse
from typing import List, Optional, Union, Dict, Iterable, Tuple, Any

from .callresult import CallResult
from .job_token_file import read_job_token_file
from .defs import (
    ENV_VERBOSE,
    ENV_HOST,
    ENV_WEB_HOST,
    ENV_OFFLINE_MODE,
    ENV_AUTH_TOKEN,
    ENV_JOB_TOKEN,
    ENV_JOB_TOKEN_FILE,
    ENV_DISABLE_VAULT_SUPPORT,
    ENV_ENABLE_ENV_CONFIG_SECTION,
    ENV_ENABLE_FILES_CONFIG_SECTION,
    ENV_API_EXTRA_RETRY_CODES,
    ENV_API_DEFAULT_REQ_METHOD,
    ENV_FORCE_MAX_API_VERSION,
    ENV_IGNORE_MISSING_CONFIG,
    MissingConfigError,
)
from .request import Request, BatchRequest  # noqa: F401
from .token_manager import TokenManager
from ..utils import get_http_session_with_retry, urllib_log_warning_setup
from ...idp.device import device_assertion
from ...backend_config import ConfigurationError
from ...debugging import get_logger
from ...debugging.log import resolve_logging_level
from ...utilities.pyhocon import ConfigTree, ConfigFactory
from ...version import __version__
from ...backend_config.utils import apply_files, apply_environment

try:
    from OpenSSL.SSL import Error as SSLError
except ImportError:
    from requests.exceptions import SSLError


class LoginError(Exception):
    pass


class MaxRequestSizeError(Exception):
    pass


class Session(TokenManager):
    """CairnML API Session class."""

    _AUTHORIZATION_HEADER = "Authorization"
    _WORKER_HEADER = (
        "X-CairnML-Worker",
        "X-Trains-Worker",
    )
    _ASYNC_HEADER = (
        "X-CairnML-Async",
        "X-Trains-Async",
    )
    _CLIENT_HEADER = (
        "X-CairnML-Client",
        "X-Trains-Client",
    )

    _async_status_code = 202
    _session_requests = 0
    _session_initial_timeout = (3.0, 10.0)
    _session_timeout = (10.0, 300.0)
    _write_session_data_size = 15000
    _write_session_timeout = (300.0, 300.0)
    _sessions_created = 0
    _ssl_error_count_verbosity = 2
    _offline_mode = ENV_OFFLINE_MODE.get()
    _offline_default_version = "2.9"
    # we want to keep track of sessions, but we also want to allow them to be collected by the GC if they are not used anymore
    _sessions_weakrefs = []

    _client = [(__package__.partition(".")[0], __version__)]

    api_version = "2.9"  # this default version should match the lowest api version we have under service
    server_version = "1.0.0"
    max_api_version = "2.9"
    feature_set = "basic"
    default_host = "http://localhost/api"
    default_web = "http://localhost"
    force_max_api_version = ENV_FORCE_MAX_API_VERSION.get()


    # TODO: add requests.codes.gateway_timeout once we support async commits
    _retry_codes = [
        requests.codes.bad_gateway,
        requests.codes.service_unavailable,
        requests.codes.bandwidth_limit_exceeded,
        requests.codes.too_many_requests,
    ]

    @property
    def device_id(self) -> Optional[str]:
        return self.__device_id

    @property
    def auth_token(self) -> Optional[str]:
        return self.__auth_token

    @property
    def host(self) -> str:
        return self.__host

    @property
    def worker(self) -> str:
        return self.__worker

    def __init__(
        self,
        worker: Optional[str] = None,
        host: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
        verbose: Optional[bool] = None,
        config: Optional[ConfigTree] = None,
        http_retries_config: Optional[dict] = None,
        **kwargs: Any
    ) -> None:
        self.__class__._sessions_weakrefs.append(weakref.ref(self))

        self._verbose = verbose if verbose is not None else ENV_VERBOSE.get()
        if logger is not None:
            self._logger = logger
        else:
            level = resolve_logging_level(ENV_VERBOSE.get(converter=str)) if self._verbose else logging.INFO
            self._logger = get_logger(level=level, stream=sys.stderr if level is logging.DEBUG else None)
        self.__worker = worker or self.get_worker_host_name()
        self.client = ", ".join(f"{item[0]}-{item[1]}" for item in self._client)

        self.__init_host = host
        self.__init_http_retries_config = http_retries_config
        self.__token_manager_kwargs = kwargs
        if config is not None:
            self.config = config
        else:
            from cairnml.config import ConfigWrapper

            self.config = ConfigWrapper._init()

        self._connect()

    @classmethod
    def add_client(cls, client: str, value: str, first: bool = True) -> None:
        # noinspection PyBroadException
        try:
            if not any(True for c in cls._client if c[0] == client):
                if first:
                    cls._client.insert(0, (client, value))
                else:
                    cls._client.append((client, value))
                cls.client = ", ".join(f"{item[0]}-{item[1]}" for item in cls._client)
        except Exception:
            pass

    def _connect(self) -> None:
        if self._offline_mode:
            return

        self._ssl_error_count_verbosity = self.config.get(
            "api.ssl_error_count_verbosity", self._ssl_error_count_verbosity
        )

        self.__host = self.__init_host or self.get_api_server_host(config=self.config)
        if not self.__host:
            raise ValueError("CairnML host was not set, check your configuration file or environment variable")
        self.__host = self.__host.strip("/")
        self.__http_retries_config = (
            self.__init_http_retries_config or self.config.get("api.http.retries", ConfigTree()).as_plain_ordered_dict()
        )

        self.__http_retries_config["status_forcelist"] = self._get_retry_codes()
        self.__http_retries_config["config"] = self.config
        self.__http_session = get_http_session_with_retry(**self.__http_retries_config)
        self.__http_session.write_timeout = self._write_session_timeout
        self.__http_session.request_size_threshold = self._write_session_data_size

        self.__max_req_size = self.config.get("api.http.max_req_size", None)
        if not self.__max_req_size:
            raise ValueError("missing max request size")

        token_expiration_threshold_sec = self.config.get("api.auth.token_expiration_threshold_sec", 60)
        req_token_expiration_sec = self.config.get(
            "api.auth.request_token_expiration_sec",
            self.config.get("api.auth.req_token_expiration_sec", None),
        )

        self.__auth_token = None
        self.__device_id = self.config.get("api.idp.device_id", None)
        self.__job_token = ENV_JOB_TOKEN.get()
        self.__job_token_file = ENV_JOB_TOKEN_FILE.get()
        self._update_default_api_method()
        if ENV_AUTH_TOKEN.get():
            self.__auth_token = ENV_AUTH_TOKEN.get()
            # if we use a token we override make sure we are at least 3600 seconds (1 hour)
            # away from the token expiration date, ask for a new one.
            if not self.__job_token_file:
                token_expiration_threshold_sec = max(token_expiration_threshold_sec, 3600)

        if not (self.__auth_token or self.__device_id or self.__job_token):
            raise MissingConfigError()

        super(Session, self).__init__(
            **self.__token_manager_kwargs,
            token_expiration_threshold_sec=token_expiration_threshold_sec,
            req_token_expiration_sec=req_token_expiration_sec
        )
        self.refresh_token()

        local_logger = self._LocalLogger(self._logger)

        # update api version from server response
        try:
            token_dict = TokenManager.get_decoded_token(self.token)
            api_version = token_dict.get("api_version")
            if not api_version:
                api_version = "2.2" if token_dict.get("env", "") == "prod" else Session.api_version

            Session.server_version = token_dict.get("server_version")
            if Session.server_version:
                self.add_client("cairnml-server", Session.server_version)

            Session.max_api_version = Session.api_version = str(api_version)
            Session.feature_set = str(token_dict.get("feature_set", self.feature_set) or "basic")
        except (jwt.DecodeError, ValueError):
            local_logger().warning(f"Failed parsing server API level, defaulting to {Session.api_version}")

        # now setup the session reporting, so one consecutive retries will show warning
        # we do that here, so if we have problems authenticating, we see them immediately
        # notice: this is across the board warning omission
        urllib_log_warning_setup(
            total_retries=self.__http_retries_config.get("total", 0),
            display_warning_after=3,
        )

        if self.force_max_api_version and self.check_min_api_version(self.force_max_api_version):
            Session.max_api_version = Session.api_version = str(self.force_max_api_version)

        # update only after we have max_api
        self.__class__._sessions_created += 1

        if self._load_vaults():
            from cairnml.config import ConfigWrapper, ConfigSDKWrapper

            ConfigWrapper.set_config_impl(self.config)
            ConfigSDKWrapper.clear_config_impl()

        self._apply_config_sections(local_logger)

        self._update_default_api_method()

    def _update_default_api_method(self) -> None:
        if not ENV_API_DEFAULT_REQ_METHOD.get(default=None) and self.config.get("api.http.default_method", None):
            def_method = str(self.config.get("api.http.default_method", None)).strip()
            if def_method.upper() not in ("GET", "POST", "PUT"):
                raise ValueError(
                    "api.http.default_method variable must be 'get', 'post' or 'put' (any case is allowed)."
                )
            Request.def_method = def_method
            Request._method = Request.def_method

    def _get_retry_codes(self) -> List[int]:
        retry_codes = set(self._retry_codes)

        extra = self.config.get("api.http.extra_retry_codes", [])
        if ENV_API_EXTRA_RETRY_CODES.get():
            extra = [s.strip() for s in ENV_API_EXTRA_RETRY_CODES.get().split(",") if s.strip()]

        for code in extra or []:
            try:
                retry_codes.add(int(code))
            except (ValueError, TypeError):
                print(f"Warning: invalid extra HTTP retry code detected: {code}")

        if retry_codes.difference(self._retry_codes):
            print(f"Using extra HTTP retry codes {sorted(retry_codes.difference(self._retry_codes))}")

        return list(retry_codes)

    def _read_vaults(self) -> Optional[List[dict]]:
        # () -> Optional[List[dict]]
        if not self.check_min_api_version("2.15") or self.feature_set == "basic":
            return

        def parse(vault: dict) -> Optional[Union[ConfigTree, dict]]:
            # noinspection PyBroadException
            try:
                d = vault.get("data", None)
                if d:
                    r = ConfigFactory.parse_string(d)
                    if isinstance(r, (ConfigTree, dict)):
                        return r
            except Exception as e:
                (self._logger or get_logger()).warning(
                    f"Failed parsing vault {vault.get('description', '<unknown>')}: {e}"
                )

        # noinspection PyBroadException
        try:
            # Use params and not data/json otherwise payload might be dropped if we're using GET with a strict firewall
            res = self.send_request("users", "get_vaults", params="enabled=true&types=config&types=config")
            if res.ok:
                vaults = res.json().get("data", {}).get("vaults", [])
                data = list(filter(None, map(parse, vaults)))
                if data:
                    return data
            elif res.status_code != 404:
                raise Exception(res.json().get("meta", {}).get("result_msg", res.text))
        except Exception as ex:
            (self._logger or get_logger()).warning(f"Failed getting vaults: {ex}")

    def _load_vaults(self) -> Optional[bool]:
        # () -> Optional[bool]
        if ENV_DISABLE_VAULT_SUPPORT.get():
            # (self._logger or get_logger()).debug("Vault support is disabled")
            return

        data = self._read_vaults()
        if data:
            self.config.set_overrides(*data)
            return True

    def _apply_config_sections(self, local_logger: Any) -> None:
        # noqa: F821
        default = self.config.get("sdk.apply_environment", False)
        if ENV_ENABLE_ENV_CONFIG_SECTION.get(default=default):
            try:
                keys = apply_environment(self.config)
                if keys:
                    print(f"Environment variables set from configuration: {keys}")
            except Exception as ex:
                local_logger().warning(f"Failed applying environment from configuration: {ex}")

        default = self.config.get("sdk.apply_files", default=False)
        if ENV_ENABLE_FILES_CONFIG_SECTION.get(default=default):
            try:
                apply_files(self.config)
            except Exception as ex:
                local_logger().warning(f"Failed applying files from configuration: {ex}")

    def _send_request(
        self,
        service: str,
        action: str,
        version: Optional[str] = None,
        method: Optional[str] = None,
        headers: Optional[dict] = None,
        auth: Optional[requests.auth.AuthBase] = None,
        data: Any = None,
        json: Optional[Union[dict, list]] = None,
        refresh_token_if_unauthorized: bool = True,
        params: Optional[dict] = None,
    ) -> Optional[requests.Response]:
        """Internal implementation for making a raw API request.
        - Constructs the api endpoint name
        - Injects the worker id into the headers
        - Allows custom authorization using a requests auth object
        - Intercepts `Unauthorized` responses and automatically attempts to refresh the session token once in this
          case (only once). This is done since permissions are embedded in the token, and addresses a case where
          server-side permissions have changed but are not reflected in the current token. Refreshing the token will
          generate a token with the updated permissions.

        NOTE: This method does not handle authorization. Credentials or token should be provided using the auth or
        headers arguments, otherwise a successful authorization depends on the session containing a valid cookie
        set during the last login call (which may not be there if the server's cookie domain does not match the URL
        we use to access the server)
        """
        if self._offline_mode:
            return None

        if not method:
            method = Request.def_method

        res = None
        host = self.host
        headers = headers.copy() if headers else {}
        for h in self._WORKER_HEADER:
            headers[h] = self.worker
        for h in self._CLIENT_HEADER:
            headers[h] = self.client

        token_refreshed_on_error = False
        url = (
            f"{host}/v{version}/{service}.{action}"  # rfh
            if version
            else f"{host}/{service}.{action}"
        )
        retry_counter = 0
        while True:
            if data and len(data) > self._write_session_data_size:
                timeout = self._write_session_timeout
            elif self._session_requests < 1:
                timeout = self._session_initial_timeout
            else:
                timeout = self._session_timeout
            try:
                if self._verbose and self._logger:
                    size = len(data or "")
                    if json and self._logger.level == logging.DEBUG:
                        size += len(json_lib.dumps(json))
                    self._logger.debug(
                        "%s: %s [%d bytes, %d headers]",
                        method.upper(),
                        url,
                        size,
                        len(headers or {}),
                    )
                res = self.__http_session.request(
                    method,
                    url,
                    headers=headers,
                    auth=auth,
                    data=data,
                    json=json,
                    timeout=timeout,
                    params=params,
                )
                if self._verbose and self._logger:
                    self._logger.debug("--> took %s", res.elapsed)
            # except Exception as ex:
            except SSLError as ex:
                retry_counter += 1
                # we should retry
                if retry_counter >= self._ssl_error_count_verbosity:
                    (self._logger or get_logger()).warning(f"SSLError Retrying {ex}")
                sleep(0.1)
                continue
            except (
                ChunkedEncodingError,
                ContentDecodingError,
                StreamConsumedError,
            ) as ex:
                retry_counter += 1
                # we should retry
                if retry_counter >= self._ssl_error_count_verbosity:
                    (self._logger or get_logger()).warning(f"Network decoding error Retrying {ex}")
                sleep(0.1)
                continue

            if (
                refresh_token_if_unauthorized
                and res.status_code == requests.codes.unauthorized
                and not token_refreshed_on_error
            ):
                # it seems we're unauthorized, so we'll try to refresh our token once in case permissions changed since
                # the last time we got the token, and try again
                self.refresh_token()
                token_refreshed_on_error = True
                # try again
                retry_counter += 1
                continue
            if res.status_code == requests.codes.service_unavailable and self.config.get(
                "api.http.wait_on_maintenance_forever", True
            ):
                (self._logger or get_logger()).warning(
                    f"Service unavailable: {host} is undergoing maintenance, retrying..."
                )
                retry_counter += 1
                continue
            break
        self._session_requests += 1
        return res

    def add_auth_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        headers[self._AUTHORIZATION_HEADER] = f"Bearer {self.token}"
        return headers

    def send_request(
        self,
        service: str,
        action: str,
        version: Optional[str] = None,
        method: Optional[str] = None,
        headers: Optional[dict] = None,
        data: Optional[Union[dict, bytes, IOBase]] = None,
        json: Optional[Any] = None,
        async_enable: bool = False,
        params: Optional[dict] = None,
    ) -> requests.Response:
        """
        Send a raw API request.
        :param service: service name
        :param action: action name
        :param version: version number (default is the preconfigured api version)
        :param method: method type (default is 'get')
        :param headers: request headers (authorization and content type headers will be automatically added)
        :param json: json to send in the request body (jsonable object or builtin types construct. if used,
                     content type will be application/json)
        :param data: Dictionary, bytes, or file-like object to send in the request body
        :param async_enable: whether request is asynchronous
        :param params: additional query parameters
        :return: requests Response instance
        """
        if not method:
            method = Request.def_method
        headers = self.add_auth_headers(headers.copy() if headers else {})
        if async_enable:
            for h in self._ASYNC_HEADER:
                headers[h] = "1"
        return self._send_request(
            service=service,
            action=action,
            version=version,
            method=method,
            headers=headers,
            data=data,
            json=json,
            params=params,
        )

    def send_request_batch(
        self,
        service: str,
        action: str,
        version: Optional[str] = None,
        headers: Optional[dict] = None,
        data: Optional[Iterable[bytes]] = None,
        json: Optional[Iterable[Any]] = None,
        method: Optional[str] = None,
    ) -> List[requests.Response]:
        """
        Send a raw batch API request. Batch requests always use application/json-lines content type.
        :param service: service name
        :param action: action name
        :param version: version number (default is the preconfigured api version)
        :param headers: request headers (authorization and content type headers will be automatically added)
        :param json: iterable of json items (batched items, jsonable objects or builtin types constructs). These will
                     be sent as a multi-line payload in the request body.
        :param data: iterable of bytes objects (batched items). These will be sent as a multi-line payload in the
                     request body.
        :param method: HTTP method
        :return: requests Response instance
        """
        if not all(isinstance(x, (list, tuple, type(None), types.GeneratorType)) for x in (data, json)):
            raise ValueError("Expecting list, tuple or generator in 'data' or 'json'")

        if not data and not json:
            # Missing data (data or json), batch requests are meaningless without it.
            return None

        if not method:
            method = Request.def_method

        headers = headers.copy() if headers else {}
        headers["Content-Type"] = "application/json-lines"

        if data:
            req_data = "\n".join(data)
        else:
            req_data = "\n".join(json_lib.dumps(x) for x in json)

        cur = 0
        results = []
        while True:
            size = self.__max_req_size
            slice = req_data[cur : cur + size]
            if not slice:
                break
            if len(slice) < size:
                # this is the remainder, no need to search for newline
                pass
            elif slice[-1] != "\n":
                # search for the last newline in order to send a coherent request
                size = slice.rfind("\n") + 1
                # readjust the slice
                slice = req_data[cur : cur + size]
                if not slice:
                    logger = self._logger or get_logger()
                    logger.error(
                        f"{service}.{action} request exceeds limit {len(req_data)} > {self.__max_req_size} bytes",
                        exc_info=logger.isEnabledFor(logging.DEBUG),
                    )
                    # skip the payload that could not be sent
                    size = req_data[cur:].find("\n") + 1
                    if size == 0:
                        # error occurred on the last package
                        break
            if slice:
                res = self.send_request(
                    method=method,
                    service=service,
                    action=action,
                    data=slice,
                    headers=headers,
                    version=version,
                )
                results.append(res)
            cur += size
        return results

    def validate_request(self, req_obj: Request) -> None:
        """Validate an API request against the current version and the request's schema"""

        try:
            # make sure we're using a compatible version for this request
            # validate the request (checks required fields and specific field version restrictions)
            validate = req_obj.validate
        except AttributeError:
            raise TypeError('"req_obj" parameter must be an backend_api.session.Request object')

        validate()

    def send_async(self, req_obj: Request) -> CallResult:
        """
        Asynchronously sends an API request using a request object.
        :param req_obj: The request object
        :type req_obj: Request
        :return: CallResult object containing the raw response, response metadata and parsed response object.
        """
        return self.send(req_obj=req_obj, async_enable=True)

    def send(
        self,
        req_obj: Request,
        async_enable: bool = False,
        headers: Optional[dict] = None,
    ) -> Optional[CallResult]:
        """
        Sends an API request using a request object.
        :param req_obj: The request object
        :type req_obj: Request
        :param async_enable: Request this method be executed in an asynchronous manner
        :param headers: Additional headers to send with request
        :return: CallResult object containing the raw response, response metadata and parsed response object.
        """
        self.validate_request(req_obj)

        if self._offline_mode:
            return None

        if isinstance(req_obj, BatchRequest):
            # TODO: support async for batch requests as well
            if async_enable:
                raise NotImplementedError("Async behavior is currently not implemented for batch requests")

            json_data = req_obj.get_json()
            res = self.send_request_batch(
                service=req_obj._service,
                action=req_obj._action,
                version=req_obj._version,
                json=json_data,
                method=req_obj._method,
                headers=headers,
            )
            # TODO: handle multiple results in this case
            if res is not None:
                try:
                    res = next(r for r in res if r.status_code != 200)
                except StopIteration:
                    # all are 200
                    res = res[0]
        else:
            res = self.send_request(
                service=req_obj._service,
                action=req_obj._action,
                version=req_obj._version,
                json=req_obj.to_dict(),
                method=req_obj._method,
                async_enable=async_enable,
                headers=headers,
            )

        call_result = CallResult.from_result(
            res=res,
            request_cls=req_obj.__class__,
            logger=self._logger,
            service=req_obj._service,
            action=req_obj._action,
            session=self,
        )

        return call_result

    @classmethod
    def _make_all_sessions_go_online(cls) -> None:
        for active_session in cls._get_all_active_sessions():
            # noinspection PyProtectedMember
            active_session._connect()

    @classmethod
    def _get_all_active_sessions(cls) -> List[Any]:
        active_sessions = []
        new_sessions_weakrefs = []
        for session_weakref in cls._sessions_weakrefs:
            session = session_weakref()
            if session:
                active_sessions.append(session)
                new_sessions_weakrefs.append(session_weakref)
        cls._sessions_weakrefs = new_sessions_weakrefs
        return active_sessions

    @classmethod
    def get_api_server_host(cls, config: Optional[ConfigTree] = None) -> str:
        if not config:
            from ...config import config_obj

            config = config_obj
        return ENV_HOST.get(
            default=(config.get("api.api_server", None) or config.get("api.host", None) or cls.default_host)
        ).rstrip("/")

    @classmethod
    def get_app_server_host(cls, config: Optional[ConfigTree] = None) -> str:
        """
        Get app server (webserver) host. This is done either from configuration or by inferring from the apiserver host.
        """
        if not config:
            from ...config import config_obj

            config = config_obj

        # get from config/environment
        web_host = ENV_WEB_HOST.get(default=config.get("api.web_server", "")).rstrip("/")
        if web_host:
            return web_host

        # return default
        host = cls.get_api_server_host(config)
        if host == cls.default_host and cls.default_web:
            return cls.default_web

        # compose ourselves
        if "://demoapi." in host:
            return host.replace("://demoapi.", "://demoapp.", 1)
        if "://api." in host:
            return host.replace("://api.", "://app.", 1)

        parsed = urlparse(host)
        if parsed.port == 8008:
            return host.replace(":8008", ":8080", 1)

        raise ValueError("Could not detect CairnML web application server")

    @classmethod
    def check_min_server_version(cls, min_server_version: str) -> bool:
        """
        Return True if Session.server_version is greater or equal >= to min_server_version
        """
        return cls._version_tuple(cls.server_version) >= cls._version_tuple(str(min_server_version))

    @classmethod
    def check_min_api_version(cls, min_api_version: str, raise_error: bool = False) -> bool:
        """
        Return True if Session.api_version is greater or equal >= to min_api_version
        """
        # If no session was created, create a default one, in order to get the backend api version.
        if cls._sessions_created <= 0:
            if cls._offline_mode:
                # allow to change the offline mode version by setting ENV_OFFLINE_MODE to the required API version
                if cls.api_version != cls._offline_default_version:
                    offline_api = ENV_OFFLINE_MODE.get(converter=lambda x: x)
                    if offline_api:
                        try:
                            # check cast to float, but leave original str if we pass it.
                            # minimum version is 2.3
                            if float(offline_api) >= 2.3:
                                cls._offline_default_version = str(offline_api)
                        except ValueError:
                            pass
                    cls.max_api_version = cls.api_version = cls._offline_default_version
            else:
                # if the requested version is lower than the minimum we support,
                # no need to actually check what the server has, we assume it must have at least our version.
                if cls._version_tuple(cls.api_version) >= cls._version_tuple(str(min_api_version)):
                    return True

                # noinspection PyBroadException
                try:
                    cls()
                except (MissingConfigError, ConfigurationError):
                    if raise_error and not ENV_IGNORE_MISSING_CONFIG.get():
                        raise
                except LoginError:
                    if raise_error:
                        raise
                except Exception:
                    pass

        return cls._version_tuple(cls.api_version) >= cls._version_tuple(str(min_api_version))

    @classmethod
    def check_min_api_server_version(cls, min_api_version: str, raise_error: bool = False) -> bool:
        """
        Return True if Session.max_api_version is greater or equal >= to min_api_version
        Notice this is the api version server reported, not the current SDK max supported api version
        """
        if cls.check_min_api_version(min_api_version, raise_error=raise_error):
            return True

        return cls._version_tuple(cls.max_api_version) >= cls._version_tuple(str(min_api_version))

    @classmethod
    def get_worker_host_name(cls) -> str:
        from ...config import dev_worker_name

        return dev_worker_name() or gethostname()

    @classmethod
    def get_clients(cls) -> List[Tuple[str, str]]:
        return cls._client

    @classmethod
    def verify_feature_set(cls, feature_set: str) -> None:
        extra_msg = {
            "advanced": "Enterprise license required"
        }.get(feature_set)
        if isinstance(feature_set, str):
            feature_set = [feature_set]
        if cls.feature_set not in feature_set:
            # make sure the feature set is fetched from a session
            if not cls._get_all_active_sessions():
                Session()  # create a temporary session such that feature_set is populated
            if cls.feature_set not in feature_set:
                extra_msg_suffix = f" ({extra_msg})" if extra_msg else ""
                raise ValueError(
                    f"CairnML-server does not support requested feature set '{feature_set}'{extra_msg_suffix}"
                )

    @staticmethod
    def _version_tuple(v: str) -> Tuple[int]:
        v = tuple(map(int, (v.split("."))))
        return v + (0,) * max(0, 3 - len(v))

    def _do_refresh_token(self, old_token: str, exp: Optional[int] = None) -> str:
        """TokenManager abstract method implementation: obtains a token for this session."""
        token_from_file = read_job_token_file(self.__job_token_file)
        if token_from_file:
            # The agent renews the job's token in this file, so a refresh takes the newest one.
            return token_from_file
        if self.__job_token:
            # A job runs with the token its agent obtained for it; the job never exchanges a key.
            return self.__job_token
        if self.__device_id:
            return self._exchange_device_assertion()
        if self.__auth_token:
            return self.__auth_token
        raise LoginError("This machine has no device key or token; run cairnml-init to enrol it")

    def _exchange_device_assertion(self) -> str:
        """Sign an assertion with this device's key and exchange it for a short-lived token."""
        assertion = device_assertion(self.__device_id, self.host)
        res = self._send_request(
            service="devices",
            action="exchange",
            version="2.8",
            method=Request.def_method,
            json={"assertion": assertion},
            refresh_token_if_unauthorized=False,
        )
        if res is None:
            raise LoginError(f"Device sign-in failed: no answer from {self.host}")
        try:
            resp = res.json()
        except ValueError:
            resp = {}
        if res.status_code != 200:
            msg = resp.get("meta", {}).get("result_msg", res.reason)
            raise LoginError(f"Device sign-in refused (error {res.status_code} from {self.host}): {msg}")
        return resp["data"]["access_token"]

    def __str__(self) -> str:
        return f"{self.__class__.__name__}[{self.host}, device={self.__device_id}]"

    class _LocalLogger:
        def __init__(self, local_logger: logging.Logger) -> None:
            self.logger = local_logger

        def __call__(self) -> logging.Logger:
            if not self.logger:
                self.logger = get_logger()
            return self.logger


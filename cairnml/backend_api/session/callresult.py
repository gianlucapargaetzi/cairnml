import logging
from typing import Optional, Dict, Type, Any

import requests

from ...backend_api.utils import get_response_cls

from .response import ResponseMeta, Response


class CallResult:
    @property
    def meta(self) -> ResponseMeta:
        return self.__meta

    @property
    def response(self) -> Response:
        return self.__response

    @property
    def response_data(self) -> Optional[Dict[str, Any]]:
        return self.__response_data

    @property
    def request_cls(self) -> Optional[Type[Any]]:
        return self.__request_cls

    def __init__(
        self,
        meta: ResponseMeta,
        response: Response = None,
        response_data: dict = None,
        request_cls: type = None,
        session: Any = None,
    ) -> None:
        assert isinstance(meta, ResponseMeta)
        if response and not isinstance(response, Response):
            raise ValueError("response should be an instance of %s" % Response.__name__)
        elif response_data and not isinstance(response_data, dict):
            raise TypeError(f"data should be an instance of {dict.__name__}")

        self.__meta = meta
        self.__response = response
        self.__request_cls = request_cls
        self.__session = session

        if response_data is not None:
            self.__response_data = response_data
        elif response is not None:
            try:
                self.__response_data = response.to_dict()
            except AttributeError as err:
                raise TypeError(f"response should be an instance of {Response.__name__}") from err
        else:
            self.__response_data = None

    @classmethod
    def _make_raw_response(
        cls,
        request_cls: Optional[Type] = None,
        service: Optional[str] = None,
        action: Optional[str] = None,
        status_code: int = 200,
        text: Optional[str] = None,
    ) -> "CallResult":
        service = service or (request_cls._service if request_cls else "unknown")
        action = action or (request_cls._action if request_cls else "unknown")
        return cls(
            request_cls=request_cls,
            meta=ResponseMeta.from_raw_data(
                status_code=status_code,
                text=text,
                endpoint="%(service)s.%(action)s" % locals(),
            ),
        )

    @classmethod
    def from_result(
        cls,
        res: requests.Response,
        request_cls: Optional[Type] = None,
        logger: Optional[logging.Logger] = None,
        service: Optional[str] = None,
        action: Optional[str] = None,
        session: Optional[Any] = None,
    ) -> "CallResult":
        """From requests result"""
        response_cls = get_response_cls(request_cls)

        if res is None:
            return cls._make_raw_response(request_cls=request_cls, service=service, action=action)

        try:
            data = res.json()
        except ValueError:
            return cls._make_raw_response(
                request_cls=request_cls,
                service=service,
                action=action,
                status_code=res.status_code,
                text=res.text,
            )
        if "meta" not in data:
            raise ValueError("Missing meta section in response payload")
        try:
            meta = ResponseMeta(**data["meta"])
            # TODO: validate meta?
            # meta.validate()
        except Exception as ex:
            raise ValueError(f"Failed parsing meta section in response payload (data={data}, error={ex})") from ex

        response = None
        response_data = None
        try:
            response_data = data.get("data", {})
            if response_cls:
                response = response_cls(**response_data)
                # TODO: validate response?
                # response.validate()
        except Exception as e:
            if logger:
                logger.warning(f"Failed parsing response: {e}")
        return cls(
            meta=meta,
            response=response,
            response_data=response_data,
            request_cls=request_cls,
            session=session,
        )

    def ok(self) -> bool:
        return self.meta.result_code == 200

    def wait(self) -> "CallResult":
        """The server answers every call when it returns, so there is nothing to wait for: the result is this one."""
        return self

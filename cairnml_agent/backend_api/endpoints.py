"""The endpoint of a request class of the SDK's generated services: the keyword arguments that name it for
send_request. The version is the one the generated class has, which is the one API version the SDK speaks."""


def endpoint(request_cls) -> dict:
    return {"service": request_cls._service, "action": request_cls._action, "version": request_cls._version}

_SaveFramesRequestNoValidate = None


def _get_save_frames_request_no_validate():
    """
    Make sure the _SaveFramesRequestNoValidate class is not created at import time.
    Creating it at import time will result in an API call being made to the server
    which won't work on machines with no network connectivity/machines with no
    ClearML credentials configured.
    """
    global _SaveFramesRequestNoValidate

    if _SaveFramesRequestNoValidate:
        return _SaveFramesRequestNoValidate

    from .save_frames_request_no_validate import _SaveFramesRequestNoValidate as _SaveFramesRequestNoValidateInternal

    _SaveFramesRequestNoValidate = _SaveFramesRequestNoValidateInternal
    return _SaveFramesRequestNoValidate

# do not import this file directly, as dereferncing datasets will result in a server call
# import from save_frames_request_no_validate_wrapped

from ...backend_api.services import datasets

_SaveFramesRequest = datasets.SaveFramesRequest if getattr(datasets, "SaveFramesRequest", None) else object


class _SaveFramesRequestNoValidate(_SaveFramesRequest):
    def validate(self, schema=None):
        pass

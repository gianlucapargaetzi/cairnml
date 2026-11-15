from ..api_proxy import ApiServiceProxy

# generated from the server schema (scripts/regenerate-api.sh), no version proxy
from . import queues


# allow us to replace the api version after we have completed the authentication process,
# and we know the backend server api version.
# ApiServiceProxy will dynamically load the correct api object based on the session api_version
auth = ApiServiceProxy("auth")
events = ApiServiceProxy("events")
models = ApiServiceProxy("models")
projects = ApiServiceProxy("projects")
tasks = ApiServiceProxy("tasks")
workers = ApiServiceProxy("workers")

__all__ = [
    "auth",
    "events",
    "models",
    "projects",
    "tasks",
    "workers",
    "queues",
]

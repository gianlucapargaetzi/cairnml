"""A ClearML-era TRAINS_ name set in the environment has no effect: the agent reads only the CAIRNML_ name."""

import pytest

from cairnml_agent import definitions
from cairnml.backend_api.session import defs as session_defs
from cairnml_agent.backend_config import defs as config_defs

# (legacy name, the CAIRNML_ name that is left unset, a reader of the setting)
LEGACY_CASES = [
    pytest.param(
        "TRAINS_DOCKER_IMAGE",
        "CAIRNML_DOCKER_IMAGE",
        lambda: definitions.ENV_DOCKER_IMAGE.get(),
        id="docker-image",
    ),
    pytest.param(
        "TRAINS_WORKER_ID",
        "CAIRNML_WORKER_ID",
        lambda: definitions.ENV_WORKER_ID.get(),
        id="worker-id",
    ),
    pytest.param(
        "TRAINS_CUDA_VERSION",
        "CAIRNML_CUDA_VERSION",
        lambda: definitions.ENVIRONMENT_CONFIG["agent.cuda_version"].get(),
        id="cuda-version",
    ),
    pytest.param(
        "TRAINS_API_HOST",
        "CAIRNML_API_HOST",
        lambda: session_defs.ENV_HOST.get(),
        id="api-host",
    ),
    pytest.param(
        "TRAINS_CONFIG_FILE",
        "CAIRNML_CONFIG_FILE",
        lambda: config_defs.LOCAL_CONFIG_FILE_OVERRIDE_VAR.get(),
        id="config-file",
    ),
    pytest.param(
        "TRAINS_CONFIG_PATH",
        "CAIRNML_CONFIG_PATH",
        lambda: config_defs.ENV_CONFIG_PATH_OVERRIDE_VAR.get(),
        id="config-path",
    ),
]


@pytest.mark.parametrize("legacy, current, read", LEGACY_CASES)
def test_a_trains_name_is_not_read(monkeypatch, legacy, current, read):
    monkeypatch.delenv(current, raising=False)
    monkeypatch.setenv(legacy, "legacy-value")

    assert not read()


def test_the_sdk_environment_names_are_cairnml_only():
    for key, names in definitions.ENVIRONMENT_SDK_PARAMS.items():
        assert len(names) == 1, key
        assert names[0].startswith("CAIRNML_"), key


def test_the_environment_name_variable_is_cairnml_env():
    assert config_defs.ENV_VAR == "CAIRNML_ENV"

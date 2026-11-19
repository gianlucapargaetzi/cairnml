"""The autoscaler and its cloud drivers do not ask the Session for a files server.

The SDK no longer knows a files server (the default storage is the server's object storage), so the Session has no
``get_files_server_host``. The start-up of the autoscaler and the driver's ``from_config`` must work with the Session's
real methods only.
"""

import logging
import os
from unittest import mock

import pytest

from cairnml.automation import auto_scaler, cloud_driver
from cairnml.automation.auto_scaler import AutoScaler, ScalerConfig
from cairnml.automation.cloud_driver import CloudDriver
from cairnml.backend_api import Session


class _Driver(CloudDriver):
    def spin_up_worker(self, *args, **kwargs):
        raise NotImplementedError

    def spin_down_worker(self, instance_id):
        raise NotImplementedError

    def kind(self):
        return "Test"

    def instance_id_command(self):
        return "echo i-0"

    def instance_type_key(self):
        return "instance_type"


@pytest.fixture
def session():
    # spec'd on the real Session class: a method the Session does not have raises AttributeError
    session = mock.Mock(spec=Session)
    session.get_api_server_host.return_value = "https://api.example.com"
    session.get_app_server_host.return_value = "https://app.example.com"
    return session


@pytest.fixture
def keep_environment():
    # AutoScaler sets the host variables for the process; restore the environment afterwards
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


def test_auto_scaler_start_up_sets_up_without_a_files_server(session, monkeypatch, keep_environment):
    monkeypatch.setattr(auto_scaler, "Session", lambda: session)
    monkeypatch.setattr(auto_scaler, "APIClient", mock.Mock())
    driver = mock.Mock(spec=CloudDriver)
    driver.kind.return_value = "Test"
    config = ScalerConfig(queues={"default": [("gpu-small", 1)]}, resource_configurations={})

    scaler = AutoScaler(config, driver, logger=logging.getLogger("test_auto_scaler"))

    assert scaler.state == auto_scaler.State.READY
    assert os.environ["CAIRNML_API_HOST"] == "https://api.example.com"


def test_cloud_driver_from_config_does_not_ask_for_a_files_server(session, monkeypatch):
    monkeypatch.setattr(cloud_driver, "Session", lambda: session)
    config = {
        "hyper_params": {
            "git_user": "user",
            "git_pass": "pass",
            "default_docker_image": "",
            "tags": "",
        },
        "configurations": {"extra_cairnml_conf": "", "extra_vm_bash_script": ""},
    }

    driver = _Driver.from_config(config)

    assert driver.api_server == "https://api.example.com"
    assert driver.web_server == "https://app.example.com"


def test_cloud_driver_user_data_has_no_files_server_line(session, monkeypatch):
    monkeypatch.setattr(cloud_driver, "Session", lambda: session)
    config = {
        "hyper_params": {"git_user": "user", "git_pass": "pass", "default_docker_image": "", "tags": ""},
        "configurations": {"extra_cairnml_conf": "", "extra_vm_bash_script": ""},
    }
    driver = _Driver.from_config(config)

    user_data = driver.gen_user_data("worker", "gpu-small", task_id="")

    assert "CAIRNML_API_HOST=https://api.example.com" in user_data
    assert "CAIRNML_WEB_HOST=https://app.example.com" in user_data
    assert "CAIRNML_FILES_HOST" not in user_data

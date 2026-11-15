"""Log calls must use %-style placeholders: the logging module ignores ``{}``."""

import logging

from cairnml_agent.commands.worker import get_task_fields
from cairnml_agent.glue.k8s import K8sIntegration


class _Response(object):
    def json(self):
        return {"data": {"tasks": []}}


class _Session(object):
    def send_request(self, **kwargs):
        return _Response()


def test_failed_task_field_lookup_logs_its_message(caplog):
    with caplog.at_level(logging.DEBUG):
        result = get_task_fields(_Session(), "task-1", ["container.image"], log=logging.getLogger("test"))

    assert result == {}
    assert "Failed obtaining values for task fields" in caplog.text
    assert "['container.image']" in caplog.text
    assert "list index out of range" in caplog.text


def test_failed_used_pods_lookup_logs_its_message(caplog):
    integration = K8sIntegration.__new__(K8sIntegration)
    integration.log = logging.getLogger("test")

    def _fail():
        raise RuntimeError("kubectl failed")

    integration._get_running_pods = _fail

    with caplog.at_level(logging.DEBUG):
        result = integration._get_used_pods()

    assert result == (-1, set())
    assert "Failed getting used pods: kubectl failed" in caplog.text

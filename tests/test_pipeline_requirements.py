from unittest import mock

from clearml.automation.controller import PipelineController
from clearml.automation.job import ClearmlJob


def test_a_pipeline_node_keeps_its_requirements_and_priority():
    node = PipelineController.Node(name="train", base_task_id=None,
                                   requirements={"cpus": 2, "ram_gb": 4}, priority=80)
    assert node.requirements == {"cpus": 2, "ram_gb": 4}
    assert node.priority == 80


def test_a_node_without_them_has_none():
    node = PipelineController.Node(name="train", base_task_id=None)
    assert node.requirements is None
    assert node.priority is None


def test_a_job_launch_passes_requirements_and_priority_to_enqueue():
    job = ClearmlJob.__new__(ClearmlJob)
    job._is_cached_task = False
    job.task = mock.sentinel.task
    with mock.patch("clearml.automation.job.Task.enqueue") as enqueue:
        assert job.launch(queue_name=None, requirements={"cpus": 2}, priority=70) is True
    enqueue.assert_called_once_with(task=mock.sentinel.task, queue_name=None,
                                    requirements={"cpus": 2}, priority=70)

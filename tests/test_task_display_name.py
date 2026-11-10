import inspect
from types import SimpleNamespace
from unittest import mock

from cairnml import Task
from cairnml.backend_api.services.v2_23 import tasks
from cairnml.backend_interface.task.task import Task as BackendTask


def make_backend_task():
    task = BackendTask.__new__(BackendTask)
    task._set_task_property = mock.MagicMock()
    task._edit = mock.MagicMock()
    return task


def test_init_and_create_accept_a_display_name_that_is_off_by_default():
    for function in (Task.init, Task.create):
        parameter = inspect.signature(function).parameters["display_name"]
        assert parameter.default is None


def test_the_label_is_sent_without_touching_the_task_name():
    task = make_backend_task()
    data = SimpleNamespace(display_name=None, name="training")
    with mock.patch.object(BackendTask, "data", new_callable=mock.PropertyMock, return_value=data):
        task.set_display_name("lr 0.1, no dropout")
    task._edit.assert_called_once_with(display_name="lr 0.1, no dropout")
    assert data.display_name == "lr 0.1, no dropout"
    assert data.name == "training"


def test_an_empty_label_removes_it():
    task = make_backend_task()
    data = SimpleNamespace(display_name="old", name="training")
    with mock.patch.object(BackendTask, "data", new_callable=mock.PropertyMock, return_value=data):
        task.set_display_name("")
        assert task.display_name == ""
    task._edit.assert_called_once_with(display_name="")


def test_the_api_models_carry_the_label():
    assert tasks.CreateRequest(name="abc", type="training", display_name="x").to_dict()["display_name"] == "x"
    assert tasks.EditRequest(task="t", display_name="y").to_dict()["display_name"] == "y"
    assert tasks.Task(display_name="z").display_name == "z"


def test_get_tasks_can_filter_by_display_name():
    assert inspect.signature(Task.get_tasks).parameters["display_name"].default is None
    assert tasks.GetAllRequest(display_name="lr 0.1").to_dict()["display_name"] == "lr 0.1"

    with mock.patch.object(Task, "_Task__get_tasks", return_value=[]) as get_tasks:
        Task.get_tasks(project_name="p", display_name="lr 0.1")
        assert get_tasks.call_args.kwargs["display_name"] == "lr 0.1"
        # the caller's filter dict is not modified
        task_filter = {"status": ["completed"]}
        Task.get_tasks(project_name="p", task_filter=task_filter, display_name="x")
        assert task_filter == {"status": ["completed"]}

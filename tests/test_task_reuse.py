import inspect

from clearml import Task


def test_every_run_creates_a_new_task_by_default():
    # Task.init used to reuse the last task of the same project and name. Each run is now logged.
    assert inspect.signature(Task.init).parameters["reuse_last_task_id"].default is False

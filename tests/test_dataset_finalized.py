from unittest import mock

import pytest

from clearml.datasets.dataset import Dataset
from clearml.backend_interface.task.task import Task


def dataset_with_status(status):
    """A Dataset whose task reports ``status``. No server: only the status is read."""
    dataset = Dataset.__new__(Dataset)
    dataset._task = mock.Mock()
    dataset._task.get_status.return_value = status
    return dataset


@pytest.mark.parametrize("status", [Task.TaskStatusEnum.completed, Task.TaskStatusEnum.published])
def test_a_finalized_dataset_refuses_new_files(status):
    """D4: a finalized dataset is immutable, so adding files to it raises instead of changing it."""
    dataset = dataset_with_status(status)
    with pytest.raises(ValueError, match="finalized and immutable"):
        dataset.add_files(path=".")
    with pytest.raises(ValueError, match="finalized and immutable"):
        dataset.add_external_files(source_url="s3://bucket/file.csv")


@pytest.mark.parametrize("status", [Task.TaskStatusEnum.created, Task.TaskStatusEnum.in_progress,
                                    Task.TaskStatusEnum.failed])
def test_an_unfinished_dataset_accepts_the_guard(status):
    """A dataset that is still being built, or failed, is not finalized, so the guard lets it through."""
    dataset = dataset_with_status(status)
    dataset._refuse_if_final("add_files")  # raises nothing

from types import SimpleNamespace
from unittest import mock

from cairnml import Task
from cairnml.automation.optimization import Objective
from cairnml.backend_api.services import tasks


def make_event(metric, variant, value, min_value, max_value):
    return tasks.LastMetricsEvent(
        metric=metric,
        variant=variant,
        value=value,
        min_value=min_value,
        max_value=max_value,
    )


def make_task(last_metrics):
    task = Task.__new__(Task)
    data = tasks.Task(last_metrics=last_metrics)
    task.reload = mock.MagicMock()
    return task, data


def test_last_scalar_metrics_are_read_from_generated_events():
    last_metrics = {
        "hash-loss": {
            "hash-train": make_event("loss", "train", 0.5, 0.1, 0.9),
            "hash-val": make_event("loss", "val", 0.7, 0.2, 1.0),
        },
        "hash-acc": {
            "hash-train": make_event("accuracy", "train", 0.8, 0.3, 0.95),
        },
    }
    task, data = make_task(last_metrics)
    with mock.patch.object(Task, "data", new_callable=mock.PropertyMock, return_value=data):
        scalars = task.get_last_scalar_metrics()

    assert scalars == {
        "loss": {
            "train": {"last": 0.5, "min": 0.1, "max": 0.9},
            "val": {"last": 0.7, "min": 0.2, "max": 1.0},
        },
        "accuracy": {"train": {"last": 0.8, "min": 0.3, "max": 0.95}},
    }


def test_a_task_without_scalars_returns_an_empty_dict():
    task, data = make_task(None)
    with mock.patch.object(Task, "data", new_callable=mock.PropertyMock, return_value=data):
        assert task.get_last_scalar_metrics() == {}


def test_the_public_task_method_returns_the_same_nested_dict():
    task, data = make_task({"h": {"s": make_event("loss", "train", 0.5, 0.1, 0.9)}})
    with mock.patch.object(Task, "data", new_callable=mock.PropertyMock, return_value=data):
        assert task.get_last_scalar_metrics() == {"loss": {"train": {"last": 0.5, "min": 0.1, "max": 0.9}}}


def test_an_objective_reads_the_last_value_and_the_extremum_from_generated_events():
    last_value = Objective("loss", "train", order="min")
    last_value._get_last_metrics_encode_field()
    title_hash, series_hash = last_value._metric
    query_result = SimpleNamespace(
        last_metrics={title_hash: {series_hash: make_event("loss", "train", 0.5, 0.1, 0.9)}},
    )
    with mock.patch.object(Task, "_query_tasks", return_value=[query_result]):
        assert last_value.get_objective("task-id") == 0.5

    extremum = Objective("loss", "train", order="min", extremum=True)
    extremum._get_last_metrics_encode_field()
    title_hash, series_hash = extremum._metric
    query_result = SimpleNamespace(
        last_metrics={title_hash: {series_hash: make_event("loss", "train", 0.5, 0.1, 0.9)}},
    )
    with mock.patch.object(Task, "_query_tasks", return_value=[query_result]):
        assert extremum.get_objective("task-id") == 0.1


def test_top_experiment_metrics_are_returned_as_the_documented_dict():
    from cairnml.automation.optimization import _last_metrics_event_as_dict

    event = make_event("accuracy per class", "cat", 0.119, 0.119, 0.782)
    assert _last_metrics_event_as_dict(event) == {
        "metric": "accuracy per class",
        "variant": "cat",
        "value": 0.119,
        "min_value": 0.119,
        "max_value": 0.782,
    }

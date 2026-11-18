"""The SDK's calls against a real server: the 14 checks of issue #121 (15 assertions).

The checks are one chain. Check 1 creates the task that later checks read back, and the ``chain`` fixture carries
what each check leaves for the next. The tests therefore run in file order, and a check whose input is missing fails
with the name of the check that should have left it (it does not pass silently). Each check asserts what the server
returns, so a wrong answer from the server or a wrong conversion in the SDK fails the check.

``RESOURCE_MONITOR`` is on: the resource monitor, which runs in a forked child, used to interleave responses between
calls (issue #123). Turning it off would hide that defect, so the checks run with the monitor as the SDK ships it.
"""

import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from cairnml import Task

pytestmark = pytest.mark.integration

RESOURCE_MONITOR = True


@pytest.fixture(scope="module")
def chain() -> dict:
    """What each check leaves for the checks after it. Empty when the module starts."""
    return {"project": f"integration-{uuid.uuid4().hex[:8]}"}


def need(chain: dict, key: str):
    if key not in chain:
        pytest.fail(f"needs {key!r}, which an earlier check should have left: that check failed", pytrace=False)
    return chain[key]


def test_01_init_connect_and_read_parameters_back(chain):
    task = Task.init(
        project_name=chain["project"],
        task_name="integration base",
        reuse_last_task_id=False,
        auto_resource_monitoring=RESOURCE_MONITOR,
    )
    got = task.connect({"lr": 0.01, "epochs": 3, "name": "x"})
    assert got == {"lr": 0.01, "epochs": 3, "name": "x"}, got
    chain["task"] = task
    chain["task_id"] = task.id


def test_02_report_scalars_and_flush(chain):
    logger = need(chain, "task").get_logger()
    for i in range(5):
        logger.report_scalar("loss", "train", value=1.0 / (i + 1), iteration=i)
    logger.flush()


def test_03_upload_artifact_file_and_object(chain):
    task = need(chain, "task")
    path = Path(tempfile.mkdtemp()) / "hello.txt"
    path.write_text("hello\n")
    task.upload_artifact("hello", artifact_object=str(path))
    task.upload_artifact("obj", artifact_object={"a": 1, "b": [1, 2]})
    task.flush(wait_for_uploads=True)


def test_04_close_leaves_the_task_completed(chain):
    task = need(chain, "task")
    task.close()
    time.sleep(2)
    status = Task.get_task(task_id=task.id).get_status()
    assert status == "completed", status


def test_05_get_task_reads_parameters_artifacts_and_scalars(chain):
    task = Task.get_task(task_id=need(chain, "task_id"))
    params = task.get_parameters_as_dict()
    assert params.get("General", {}).get("lr") in (0.01, "0.01"), params
    assert "hello" in task.artifacts and "obj" in task.artifacts, list(task.artifacts)
    assert task.artifacts["obj"].get() == {"a": 1, "b": [1, 2]}
    scalars = task.get_reported_scalars()
    assert scalars["loss"]["train"]["y"][-1] is not None, scalars


def test_06_get_tasks_and_query_tasks_by_project(chain):
    task_id = need(chain, "task_id")
    found = Task.get_tasks(project_name=chain["project"])
    assert any(t.id == task_id for t in found), [t.id for t in found]
    ids = Task.query_tasks(project_name=chain["project"])
    assert task_id in ids, ids


def test_07_clone_and_set_parameters_as_dict(chain):
    base = Task.get_task(task_id=need(chain, "task_id"))
    clone = Task.clone(source_task=base, name="integration clone", project=base.project)
    clone.set_parameters_as_dict({"General": {"lr": 0.5}})
    assert clone.get_status() == "created", clone.get_status()
    assert float(clone.get_parameters_as_dict()["General"]["lr"]) == 0.5
    chain["clone"] = clone


def test_08_enqueue_then_dequeue(chain):
    clone = need(chain, "clone")
    Task.enqueue(clone, queue_name="default")
    assert clone.get_status() == "queued", clone.get_status()
    Task.dequeue(clone)
    assert clone.get_status() == "created", clone.get_status()


def test_09_reset_archive_and_move_to_project(chain):
    clone = need(chain, "clone")
    clone.reset(force=True)
    clone.set_archived(True)
    clone.set_archived(False)
    clone.move_to_project(new_project_name=chain["project"] + "-moved")


def test_10_publish_the_base_task_and_delete_the_clone(chain):
    task_id = need(chain, "task_id")
    Task.get_task(task_id=task_id).publish()
    assert Task.get_task(task_id=task_id).get_status() == "published"
    need(chain, "clone").delete(delete_artifacts_and_models=True)


def test_11_output_model_update_weights_then_query_models(chain):
    from cairnml import Model, OutputModel

    task = Task.init(
        project_name=chain["project"],
        task_name="integration model",
        reuse_last_task_id=False,
        auto_resource_monitoring=RESOURCE_MONITOR,
    )
    weights = Path(tempfile.mkdtemp()) / "w.bin"
    weights.write_bytes(b"0" * 64)
    model = OutputModel(task=task, name="integration-model", framework="pytorch")
    model.update_weights(weights_filename=str(weights))
    task.close()
    found = Model.query_models(project_name=chain["project"], model_name="integration-model")
    assert found, "model not found"


def test_12_logger_text_table_and_debug_image(chain):
    import numpy as np
    import pandas as pd

    task = Task.init(
        project_name=chain["project"],
        task_name="integration media",
        reuse_last_task_id=False,
        auto_resource_monitoring=RESOURCE_MONITOR,
    )
    logger = task.get_logger()
    logger.report_text("hello console")
    logger.report_table("t", "s", iteration=0, table_plot=pd.DataFrame({"a": [1, 2]}))
    logger.report_image("img", "s", iteration=0, image=np.zeros((8, 8, 3), dtype="uint8"))
    logger.flush()
    task.close()


def test_13_offline_session_is_recorded_and_imported(chain):
    # The parent's Task.init exported its task id: the child process must not attach to it, so the CAIRNML_ and
    # TRAINS_ variables are left out of its environment. HOME is kept: the child reads the same configuration.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CAIRNML_", "TRAINS_"))}
    code = (
        "from cairnml import Task\n"
        "Task.set_offline(True)\n"
        f"t = Task.init(project_name={chain['project']!r}, task_name='integration offline', reuse_last_task_id=False,"
        f" auto_resource_monitoring={RESOURCE_MONITOR})\n"
        "t.connect({'a': 1}); t.get_logger().report_scalar('l', 's', 1.0, 0)\n"
        "folder = str(t.get_offline_mode_folder()); t.close()\n"
        "print('FOLDER', folder)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, (out.stdout + out.stderr)[-500:]
    folder = [line for line in out.stdout.splitlines() if line.startswith("FOLDER ")][-1][len("FOLDER ") :]
    assert Task.import_offline_session(folder)


def test_14_repository_calls_through_the_sdk_helpers(chain):
    # The two repository calls the SDK makes for a person: the list (get_all) and the lookup by address (find). The
    # address is one the server does not hold: its answer is "no such dataset", which is a correct answer. A refusal
    # for any other reason (an endpoint or a version the server does not know) fails the check.
    from cairnml.datasets import credentials
    from cairnml.datasets.errors import DatasetError

    repositories = credentials.fetch_repository_list()
    assert isinstance(repositories, list), repositories
    try:
        resolved = credentials.resolve_dataset("https://github.com/gianlucapargaetzi/does-not-exist")
    except DatasetError as exc:
        assert "no such dataset" in str(exc), str(exc)
    else:
        assert resolved == {}, resolved

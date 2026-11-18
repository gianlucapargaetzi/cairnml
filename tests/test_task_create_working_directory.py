import subprocess

import pytest

from cairnml import Task
from cairnml.backend_interface.task.populate import CreateAndPopulate


@pytest.fixture
def no_server(monkeypatch):
    # A dry run still resolves the project id; the id is not needed to check the task definition
    monkeypatch.setattr(Task, "get_project_id", classmethod(lambda cls, project_name: "project-id"))


@pytest.fixture
def local_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('train')\n")
    git = ["git", "-c", "user.email=test@example.com", "-c", "user.name=test"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run([*git, "-C", str(repo), "add", "."], check=True)
    subprocess.run([*git, "-C", str(repo), "commit", "-qm", "init"], check=True)
    return repo


def test_local_repository_without_working_directory_uses_repository_root(no_server, local_repo):
    populate = CreateAndPopulate(
        project_name="p",
        task_name="t",
        repo=str(local_repo),
        branch="main",
        script="train.py",
        add_task_init_call=False,
        working_directory=None,
    )

    task = populate.create_task(dry_run=True)

    assert task["script"]["working_dir"] == "."
    assert task["script"]["entry_point"] == "train.py"


def test_remote_repository_without_working_directory_uses_repository_root(no_server):
    populate = CreateAndPopulate(
        project_name="p",
        task_name="t",
        repo="https://github.com/example/repo.git",
        branch="main",
        script="train.py",
        add_task_init_call=False,
        working_directory=None,
    )

    task = populate.create_task(dry_run=True)

    assert task["script"]["working_dir"] == "."
    assert task["script"]["entry_point"] == "train.py"

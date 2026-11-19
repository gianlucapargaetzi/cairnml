from typing import Optional

from cairnml.backend_api.services import tasks as tasks_api


class TaskRuntime(object):
    def __init__(self, session):
        self._session = session

    def get_task_runtime(self, task_id) -> Optional[dict]:
        try:
            data = self._session.send_api(self._session.current(tasks_api.GetByIdRequest)(task_id)).to_dict()
            if not data or "task" not in data:
                raise ValueError("empty data in result")
            return data["task"].get("runtime", {})
        except Exception as ex:
            print(f"ERROR: Failed getting runtime properties for task {task_id}: {ex}")

    def update_task_runtime(self, task_id: str, runtime: dict) -> bool:
        task_runtime = self.get_task_runtime(task_id) or {}
        task_runtime.update(runtime)

        try:
            self._session.send_api(
                self._session.current(tasks_api.EditRequest)(task=task_id, force=True, runtime=task_runtime)
            )
            return True
        except Exception as ex:
            print("WARNING: failed setting custom runtime properties for task '{}': {}".format(task_id, ex))

        return False

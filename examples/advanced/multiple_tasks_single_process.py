from clearml import Task


for i in range(3):
    task = Task.init(
        project_name="examples",
        task_name=f"Same process, Multiple tasks, Task #{i}",
    )
    print(f"Task #{i} running")
    print(f"Task #{i} done :) ")
    task.close()

"""A local two-step PipelineDecorator run against a real server (issue #166).

The pipeline controller sets its ``version`` user property through ``Task.set_user_properties``; before the fix that call
raised a TypeError on a real server, so no pipeline could start. The pipeline runs as a script in a subprocess (decorators
need a source file), and the check reads the controller task back from the server, so a step that failed and was
swallowed cannot pass.
"""

import os
import subprocess
import sys
import textwrap
import uuid

import pytest

pytestmark = pytest.mark.integration

PIPELINE_VERSION = "1.0.0"

SCRIPT = textwrap.dedent(
    """\
    from cairnml import PipelineDecorator


    @PipelineDecorator.component(return_values=["x"])
    def step_one(n):
        return n + 1


    @PipelineDecorator.component(return_values=["y"])
    def step_two(x):
        return x * 2


    @PipelineDecorator.pipeline(name={name!r}, project={project!r}, version={version!r})
    def main(n):
        y = step_two(step_one(n))
        print("pipeline done: {{}}".format(y))
        return y


    if __name__ == "__main__":
        PipelineDecorator.run_locally()
        main(3)
    """
)


def test_a_local_two_step_pipeline_runs_to_its_last_step(tmp_path):
    from cairnml import Task

    name = f"integration pipeline {uuid.uuid4().hex[:8]}"
    project = f"integration pipelines {uuid.uuid4().hex[:8]}"
    script = tmp_path / "pipeline_script.py"
    script.write_text(SCRIPT.format(name=name, project=project, version=PIPELINE_VERSION), encoding="utf-8")

    # The parent's Task.init may have exported a task id: the child must not attach to it, so the CAIRNML_ and TRAINS_
    # variables are left out of its environment. HOME is kept: the child reads the same configuration.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CAIRNML_", "TRAINS_"))}
    out = subprocess.run(
        [sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=300, cwd=tmp_path
    )
    output = out.stdout + out.stderr
    assert out.returncode == 0, output[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "pipeline done: 8", output[-2000:]

    controller = Task.get_task(project_name=project, task_name=name)
    assert controller is not None, "the pipeline controller task was not found on the server"
    assert controller.get_user_properties(value_only=True).get("version") == PIPELINE_VERSION

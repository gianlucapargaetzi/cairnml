"""The Kubernetes glue must parse only kubectl's stdout, never its stderr (based on the proposal in #53)."""

import json
import logging
import os
import stat

import pytest

from cairnml_agent.glue import k8s
from cairnml_agent.glue import utilities as glue_utilities
from cairnml_agent.helper.process import stringify_bash_output

WARNING = "Warning: policy/v1beta1 is deprecated"
POD_LIST = {"items": [{"metadata": {"name": "pod-a"}}]}


def _install_fake_kubectl(tmp_path, monkeypatch, exit_code=0):
    script = tmp_path / "kubectl"
    script.write_text(
        "#!/bin/sh\necho '{warning}' >&2\necho '{payload}'\nexit {code}\n".format(
            warning=WARNING, payload=json.dumps(POD_LIST), code=exit_code
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", "{}:/usr/bin:/bin".format(tmp_path))


def _integration():
    obj = k8s.K8sIntegration.__new__(k8s.K8sIntegration)
    obj.log = logging.getLogger("test_k8s_glue_stderr")
    obj.namespace = "default"
    obj._agent_label = "agent=test"
    obj.using_jobs = True
    return obj


pytestmark = pytest.mark.skipif(os.name == "nt", reason="uses a POSIX shell script as fake kubectl")


def test_get_pods_parses_only_stdout(tmp_path, monkeypatch):
    _install_fake_kubectl(tmp_path, monkeypatch)
    assert _integration().get_pods() == POD_LIST["items"]


def test_a_failing_kubectl_gives_no_output(tmp_path, monkeypatch):
    _install_fake_kubectl(tmp_path, monkeypatch, exit_code=1)
    assert _integration().get_pods() is None


def test_the_glue_uses_its_own_stderr_dropping_wrapper():
    assert k8s.get_bash_output is glue_utilities.get_bash_output


@pytest.mark.parametrize("fn", [k8s.get_bash_output, glue_utilities.get_bash_output])
def test_the_issue_snippet_drops_stderr(fn):
    cmd = "echo 'pod-a value'; echo 'warning: deprecated' >&2"
    assert stringify_bash_output(fn(cmd)).splitlines() == ["pod-a value"]

"""A task cannot give its container access to the host through its docker arguments."""

from cairnml_agent.helper.docker_args import DockerArgsSanitizer


def _shipped_defaults():
    """The default block list, read from the shipped configuration, so the test cannot drift from it."""
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "cairnml_agent" / "backend_api" / "config" / "default"
            / "agent.conf").read_text()
    block = re.search(r"forbidden_docker_args: \[(.*?)\]", text, re.S).group(1)
    return {"agent.forbidden_docker_args": re.findall(r'"([^"]+)"', block),
            "agent.docker_args_extra_precedes_task": True}


DEFAULTS = _shipped_defaults()


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _merge(task_args):
    return DockerArgsSanitizer.merge_docker_args(_Config(DEFAULTS), task_args, [])


def test_a_privileged_container_is_stripped():
    base, stripped = _merge(["--privileged", "--shm-size=2g"])
    assert "--privileged" not in base
    assert "privileged" in stripped
    assert "--shm-size=2g" in base


def test_a_host_root_bind_mount_is_stripped():
    base, stripped = _merge(["-v", "/:/host", "--shm-size=2g"])
    assert "/:/host" not in base
    assert "v" in stripped


def test_mount_and_capabilities_are_stripped():
    base, stripped = _merge(["--mount", "type=bind,source=/,target=/h", "--cap-add=SYS_ADMIN", "--device=/dev/sda"])
    assert "mount" in stripped and "cap-add" in stripped and "device" in stripped
    assert not any("SYS_ADMIN" in a or "/dev/sda" in a or "source=/" in a for a in base)


def test_harmless_switches_are_kept():
    base, stripped = _merge(["--shm-size=2g", "-e", "A=1", "--gpus", "all"])
    assert base == ["--shm-size=2g", "-e", "A=1", "--gpus", "all"]
    assert stripped == []

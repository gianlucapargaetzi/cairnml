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


def test_a_short_switch_with_its_value_attached_is_stripped():
    # docker reads "-v/:/host" as -v with the value "/:/host"
    for args in (["-v/:/host"], ["-v=/:/host"], ["-v /:/host"], ["-itv/:/host"]):
        base, stripped = _merge(args)
        assert base == [], args
        assert "v" in stripped, args


def test_a_stripped_switch_takes_its_value_with_it():
    base, stripped = _merge(["-v/:/host", "--shm-size=2g", "-v", "/:/other"])
    assert base == ["--shm-size=2g"]
    assert stripped == ["v"]


def test_a_kept_switch_keeps_the_words_after_it_and_a_stripped_one_drops_them():
    base, stripped = _merge(["--shm-size=2g", "A", "-v", "/:/host", "--gpus", "all"])
    assert base == ["--shm-size=2g", "A", "--gpus", "all"]
    assert stripped == ["v"]


def test_host_files_are_read_or_written_only_by_the_admin():
    for args in (["--env-file", "/home/u/.cairnml/agent/device.key"], ["--env-file=/etc/shadow"],
                 ["--label-file", "/etc/passwd"], ["--log-opt", "path=/etc/x"], ["--log-driver", "none"],
                 ["--cidfile", "/tmp/x"], ["--group-add", "docker"], ["--device-cgroup-rule", "c 1:* rwm"]):
        base, stripped = _merge(args)
        assert base == [], args
        assert stripped, args


def test_harmless_short_switches_with_values_are_kept():
    base, stripped = _merge(["-e", "A=1", "-p8080:80", "-u", "1000"])
    assert stripped == []
    assert base == ["-e", "A=1", "-p8080:80", "-u", "1000"]


def test_an_admin_can_allow_a_switch_through_the_configuration():
    config = _Config(DEFAULTS)
    config["agent.forbidden_docker_args"] = [s for s in DEFAULTS["agent.forbidden_docker_args"] if s != "v"]
    base, stripped = DockerArgsSanitizer.merge_docker_args(config, ["-v/data:/data"], [])
    assert base == ["-v/data:/data"]
    assert stripped == []


def test_an_empty_list_turns_the_check_off():
    config = _Config(DEFAULTS)
    config["agent.forbidden_docker_args"] = []
    base, stripped = DockerArgsSanitizer.merge_docker_args(config, ["--privileged"], [])
    assert base == ["--privileged"]
    assert stripped == []


def test_the_admin_extra_arguments_are_checked_too():
    base, stripped = DockerArgsSanitizer.merge_docker_args(_Config(DEFAULTS), [], ["--privileged", "-e", "A=1"])
    assert "--privileged" not in base
    assert base == ["-e", "A=1"]
    assert stripped == ["privileged"]


def test_no_shipped_docker_mount_covers_the_agent_key_folder():
    """The job containers must not see ~/.cairnml/agent, where the device key is kept."""
    import re
    from pathlib import Path

    from cairnml_agent.idp.device import DEVICE_KEY_PATH

    text = (Path(__file__).resolve().parents[1] / "cairnml_agent" / "backend_api" / "config" / "default"
            / "agent.conf").read_text()
    key_folder = str(DEVICE_KEY_PATH.parent.relative_to(Path.home()))
    for path in re.findall(r"~/(\.cairnml[^\s\"']*)", text):
        assert path != key_folder and not path.startswith(key_folder + "/"), path

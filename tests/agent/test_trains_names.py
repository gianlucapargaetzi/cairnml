"""The ClearML-era `trains` names of the agent (issue #132): the legacy file warns, the kept names are listed, the rest is gone."""

import re
import subprocess
from pathlib import Path

import pytest
from cairnml.backend_config import environment

from cairnml_agent.backend_config import defs
from cairnml_agent.backend_config.config import Config
from cairnml_agent.glue import k8s

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(monkeypatch, tmp_path):
    """A scratch home: the agent's three local files point into it and no override variable is set."""
    monkeypatch.delenv("CAIRNML_CONFIG_FILE", raising=False)
    monkeypatch.delenv("CAIRNML_CONFIG_PATH", raising=False)
    monkeypatch.setattr(
        defs,
        "LOCAL_CONFIG_FILES",
        [
            str(tmp_path / "trains.conf"),
            str(tmp_path / "cairnml-agent.conf"),
            str(tmp_path / ".cairnml" / "agent" / "cairnml-agent.conf"),
        ],
    )
    environment._warned_legacy_keys.clear()
    yield tmp_path
    environment._warned_legacy_keys.clear()


def load_config():
    config = Config(verbose=False)
    config.reload()
    return config


# ----- the legacy file ~/trains.conf


def test_the_legacy_file_is_read_with_a_warning_that_names_it_and_the_agents_file(home, capsys):
    legacy = home / "trains.conf"
    legacy.write_text("legacy_marker = 1\n")

    config = load_config()

    err = capsys.readouterr().err
    assert config.get("legacy_marker") == 1, "the file is still read"
    assert str(legacy) in err
    assert str(home / ".cairnml" / "agent" / "cairnml-agent.conf") in err
    assert "rename it to cairnml.conf" not in err, "the agent does not read cairnml.conf"


def test_the_legacy_file_warns_once_for_the_whole_process(home, capsys):
    (home / "trains.conf").write_text("")

    load_config()
    load_config()

    assert capsys.readouterr().err.count("trains.conf") == 1


def test_no_warning_without_the_legacy_file(home, capsys):
    (home / "cairnml-agent.conf").write_text("marker = 1\n")

    load_config()

    assert capsys.readouterr().err == ""


def test_no_warning_when_the_override_variable_replaces_the_files(home, capsys, monkeypatch):
    (home / "trains.conf").write_text("")
    override = home / "custom.conf"
    override.write_text("marker = 1\n")
    monkeypatch.setenv("CAIRNML_CONFIG_FILE", str(override))

    load_config()

    assert "trains.conf" not in capsys.readouterr().err


# ----- the k8s glue option trains_conf_file


@pytest.fixture
def pod_conf(tmp_path):
    path = tmp_path / "pod.conf"
    path.write_text("pod conf content")
    return path


def test_the_old_k8s_option_still_gives_the_pod_file_with_a_warning(pod_conf, capsys):
    environment._warned_legacy_keys.clear()

    content = k8s.pod_conf_file_content(None, {"trains_conf_file": str(pod_conf)})

    err = capsys.readouterr().err
    assert content == "pod conf content"
    assert "trains_conf_file" in err and "cairnml_conf_file" in err


def test_the_cairnml_k8s_option_gives_the_pod_file_without_a_warning(pod_conf, capsys):
    environment._warned_legacy_keys.clear()

    assert k8s.pod_conf_file_content(str(pod_conf), {}) == "pod conf content"
    assert capsys.readouterr().err == ""


def test_the_cairnml_k8s_option_wins_over_the_old_one(pod_conf, tmp_path):
    other = tmp_path / "other.conf"
    other.write_text("other content")

    assert k8s.pod_conf_file_content(str(pod_conf), {"trains_conf_file": str(other)}) == "pod conf content"


def test_no_k8s_option_gives_no_pod_file():
    assert k8s.pod_conf_file_content(None, {}) is None


# ----- no trains name is left except the ones kept on purpose

# Every case-insensitive "trains" in the agent's code and Docker scripts, with the reason it stays. A new one fails
# the test below: it is either renamed or listed here with its reason.
KEPT_ON_PURPOSE = {
    ("cairnml_agent/backend_config/defs.py", "~/trains.conf"): "the legacy file is still read, with a warning",
    ("cairnml_agent/backend_config/config.py", "trains.conf"): "the warning about the legacy file",
    ("cairnml_agent/commands/worker.py", 'collect_package=["trains"]'): "the PyPI package of the old SDK, not our name",
    ("cairnml_agent/commands/worker.py", '"trains" is the PyPI name'): "the comment of the line above",
    (
        "cairnml_agent/glue/k8s.py",
        "trains_conf_file",
    ): "the old option of the k8s glue, kept as a fallback with a warning",
}


def test_the_only_trains_names_left_are_the_ones_kept_on_purpose():
    tracked = subprocess.run(
        ["git", "ls-files", "cairnml_agent", "docker"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    found = []
    for name in tracked:
        if "/_vendor/" in name:
            continue
        for line in (ROOT / name).read_text(encoding="utf-8", errors="ignore").splitlines():
            if re.search("trains", line, re.IGNORECASE):
                found.append((name, line.strip()))
    unlisted = [
        (name, line)
        for name, line in found
        if not any(name == kept_file and kept in line for kept_file, kept in KEPT_ON_PURPOSE)
    ]
    assert unlisted == []
    for kept_file, kept in KEPT_ON_PURPOSE:
        assert any(name == kept_file and kept in line for name, line in found), f"{kept} is no longer in {kept_file}"

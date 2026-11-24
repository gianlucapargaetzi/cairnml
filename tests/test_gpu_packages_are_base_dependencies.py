"""NVIDIA GPU monitoring needs no extra: its two packages are base dependencies (cairnml#195).

The extra ``gpu`` stays as an empty alias, so documented install commands keep working, and the ``agent`` extra no longer
repeats the packages.
"""

import ast
import pathlib
import re

from packaging.requirements import Requirement

from cairnml import agent_launcher

ROOT = pathlib.Path(__file__).resolve().parent.parent
GPU_PACKAGES = ("nvidia-ml-py", "gpustat")


def base_requirements():
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    return [Requirement(line) for line in lines if line.strip() and not line.lstrip().startswith("#")]


def extra(name):
    """The list of an extra in pyproject.toml, read as text so that Python 3.9 and 3.10 (no tomllib) work too."""
    text = (ROOT / "pyproject.toml").read_text()
    section = text.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
    found = re.search(rf"^{re.escape(name)} = (\[.*?\n?\])\s*$", section, re.DOTALL | re.MULTILINE)
    assert found, f"extra {name} not found"
    return [Requirement(item) for item in ast.literal_eval(found.group(1))]


def names(requirements):
    return {requirement.name for requirement in requirements}


def test_both_packages_are_base_dependencies():
    assert set(GPU_PACKAGES) <= names(base_requirements())


def test_the_base_pins_are_the_ones_the_gpu_extra_had():
    pins = {requirement.name: str(requirement.specifier) for requirement in base_requirements()}
    assert set(pins["nvidia-ml-py"].split(",")) == {">=13.615.71"}
    assert set(pins["gpustat"].split(",")) == {">=1.1.1", "<2"}


def test_the_gpu_extra_is_an_empty_alias():
    assert extra("gpu") == []


def test_the_agent_extra_does_not_repeat_the_packages():
    assert not set(GPU_PACKAGES) & names(extra("agent"))


def test_the_agent_launcher_does_not_ask_for_the_packages():
    assert not set(GPU_PACKAGES) & set(agent_launcher.AGENT_ONLY)

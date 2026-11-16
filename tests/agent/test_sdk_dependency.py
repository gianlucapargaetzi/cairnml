"""The agent declares the SDK package as a dependency (issue 47)."""

from pathlib import Path

from packaging.requirements import Requirement

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"


def _requirements():
    lines = (line.split("#")[0].strip() for line in REQUIREMENTS.read_text().splitlines())
    return [Requirement(line) for line in lines if line]


def test_the_agent_requires_the_sdk():
    sdk = [r for r in _requirements() if r.name == "cairnml"]
    assert len(sdk) == 1, "requirements.txt must hold exactly one cairnml requirement"
    assert sdk[0].specifier.contains("0.0.7")
    assert not sdk[0].specifier.contains("0.1.0")

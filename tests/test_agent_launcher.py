"""The cairnml-agent script refuses to run without the agent extra, and says which distributions are missing."""

import importlib.metadata

import pytest

from cairnml import agent_launcher

AGENT_ONLY_NAMES = (
    "virtualenv",
    "requirements-parser",
    "setuptools",
    "packaging",
)


def _hide(monkeypatch, hidden):
    real = importlib.metadata.distribution

    def distribution(name, *args, **kwargs):
        if name == hidden:
            raise importlib.metadata.PackageNotFoundError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.metadata, "distribution", distribution)


@pytest.mark.parametrize("name", AGENT_ONLY_NAMES)
def test_one_missing_agent_distribution_stops_the_script(monkeypatch, capsys, name):
    _hide(monkeypatch, name)

    with pytest.raises(SystemExit) as stop:
        agent_launcher.main()

    assert stop.value.code == 1
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1
    assert "cairnml[agent]" in err[0]
    assert name in err[0]


def test_the_check_passes_when_every_agent_distribution_is_installed(monkeypatch):
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: object())

    assert agent_launcher.missing_agent_distributions() == []
    assert agent_launcher.require_agent_extra() is None

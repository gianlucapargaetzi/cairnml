"""The Python versions that pyproject.toml lists (PyPI shows them as the badge) are the versions CI runs the tests on.

A version in the list that CI does not run is a claim nobody has checked; a version CI runs that the list leaves out is
support nobody can see. Either one fails here.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
CI = ROOT / ".github" / "workflows" / "ci.yml"

pytestmark = pytest.mark.skipif(
    not (PYPROJECT.is_file() and CI.is_file()), reason="needs the source tree: pyproject.toml and the CI workflow"
)


def _version(text):
    return tuple(int(part) for part in text.split("."))


def _listed():
    return sorted(re.findall(r'"Programming Language :: Python :: (3\.\d+)"', PYPROJECT.read_text()), key=_version)


def _run_by_ci():
    text = CI.read_text()
    first_job, _, rest = text.partition("\n  python-versions:")
    versions = set(re.findall(r'python-version: "(3\.\d+)"', first_job.split("\n  package:")[0]))
    matrix = re.search(r"matrix:\s*\n\s*python: \[([^\]]*)\]", rest)
    versions.update(re.findall(r"3\.\d+", matrix.group(1)) if matrix else [])
    return sorted(versions, key=_version)


def test_the_listed_versions_are_the_versions_ci_runs():
    assert _listed(), "pyproject.toml lists no Python version"
    assert _listed() == _run_by_ci()


def test_the_oldest_listed_version_is_the_one_python_requires():
    requires = re.search(r'requires-python\s*=\s*">=(3\.\d+)"', PYPROJECT.read_text())
    assert requires, "requires-python is not a plain '>=3.x'"
    assert requires.group(1) == _listed()[0]

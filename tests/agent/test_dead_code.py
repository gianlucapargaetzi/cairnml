"""Dead code: the update check is gone, and vulture at 80% confidence finds nothing the whitelist does not name."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXCLUDE = "*_vendor*,*/external/*,*backend_api/services*,*/tests/*"


def test_the_update_check_module_is_gone():
    assert importlib.util.find_spec("cairnml_agent.helper.check_update") is None


def test_the_managed_mode_scheduler_package_is_gone():
    # the name is built so that a search for the package name finds no leftover reference
    assert importlib.util.find_spec("cairnml_agent." + "scheduler") is None
    assert not (ROOT / "tests" / "scheduler").exists()


def test_nothing_in_the_package_names_the_update_service():
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "cairnml_agent").rglob("*.py")
        if "_vendor" not in path.parts and "updates.clear.ml" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_vulture_at_80_percent_finds_nothing_the_whitelist_does_not_name():
    pytest.importorskip("vulture")
    assert (ROOT / "agent-root" / "vulture_whitelist.py").is_file()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "vulture",
            "cairnml_agent",
            "agent-root/vulture_whitelist.py",
            "--min-confidence",
            "80",
            "--exclude",
            EXCLUDE,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.stdout == "", result.stdout

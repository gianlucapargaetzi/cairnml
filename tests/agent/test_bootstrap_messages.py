"""The bootstrap notice and the install-bootstrap command are gone: nothing points at a repository that does not exist."""

from pathlib import Path

import pytest

from cairnml_agent.interface import get_parser

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "cairnml_agent"
DEAD_REFERENCES = ["clear.ml", "github.com/clearml", "install-bootstrap", "check-bootstrap", "check_for_latest"]


def test_the_command_line_has_no_install_bootstrap():
    parser = get_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["install-bootstrap"])


def test_the_daemon_has_no_check_bootstrap_flag():
    parser = get_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["daemon", "--check-bootstrap"])


def test_no_message_in_the_package_names_a_removed_command_or_the_upstream_project():
    offenders = []
    for path in PACKAGE.rglob("*"):
        if path.is_dir() or "_vendor" in path.parts or "backend_api/services" in path.as_posix():
            continue
        if path.suffix not in {".py", ".conf"}:
            continue
        text = path.read_text(encoding="utf-8")
        for reference in DEAD_REFERENCES:
            if reference in text:
                offenders.append("{}: {}".format(path.relative_to(ROOT), reference))

    assert offenders == []

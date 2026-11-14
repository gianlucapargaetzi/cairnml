"""Environment variables: one name per setting, every name documented, legacy names not read."""

import ast
import pathlib
import re

import cairnml

ROOT = pathlib.Path(cairnml.__file__).parent.parent
PACKAGE = ROOT / "cairnml"
TABLE = ROOT / "docs" / "environment.md"


def _sdk_sources():
    return [p for p in PACKAGE.rglob("*.py") if "backend_api/services" not in p.as_posix()]


def _names_read_in_code():
    """Every CAIRNML_ name passed to EnvEntry(...) or read with os.environ / os.getenv / read_env_with_legacy."""
    found = set()
    for path in _sdk_sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            value = node.args[0].value
            if not isinstance(value, str) or not value.startswith("CAIRNML_"):
                continue
            callee = ast.unparse(node.func)
            if callee.endswith(("EnvEntry", "getenv", "environ.get", "environ.pop", "environ.setdefault",
                                "read_env_with_legacy")):
                found.add(value)
    return found


def _names_in_table():
    return set(re.findall(r"^\|\s*`(CAIRNML_[A-Z0-9_]+)`", TABLE.read_text(encoding="utf-8"), re.M)) if TABLE.exists() else set()


def test_the_sdk_source_names_no_trains_variable():
    hits = [f"{p.relative_to(PACKAGE)}:{i}" for p in _sdk_sources()
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if "TRAINS_" in line]
    assert hits == []


def test_every_name_the_code_reads_is_in_the_environment_table():
    assert TABLE.exists(), "docs/environment.md is missing"
    assert sorted(_names_read_in_code() - _names_in_table()) == []


def test_the_table_lists_no_name_the_code_does_not_read():
    assert sorted(_names_in_table() - _names_read_in_code()) == []


def test_a_legacy_name_is_not_read(monkeypatch):
    from cairnml.config.defs import TASK_ID_ENV_VAR

    monkeypatch.delenv("CAIRNML_TASK_ID", raising=False)
    monkeypatch.setenv("TRAINS_TASK_ID", "legacy-task")
    assert not TASK_ID_ENV_VAR.get()

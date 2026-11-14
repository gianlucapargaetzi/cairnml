"""The SDK ships no code that cannot run and no request to an update service."""

import ast
import pathlib
import subprocess

import cairnml

PACKAGE = pathlib.Path(cairnml.__file__).parent


def _python_files():
    return [p for p in PACKAGE.rglob("*.py") if "backend_api/services" not in p.as_posix() and "_vendor" not in p.as_posix()]


def _is_disabled_branch(node):
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.BoolOp) or not isinstance(node.test.op, ast.And):
        return False
    return isinstance(node.test.values[0], ast.Constant) and node.test.values[0].value is False


def test_no_branch_starts_with_a_constant_false_condition():
    found = [f"{p.relative_to(PACKAGE)}:{n.lineno}" for p in _python_files()
             for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))) if _is_disabled_branch(n)]
    assert found == []


def test_no_module_contacts_the_update_service():
    hits = [str(p.relative_to(PACKAGE)) for p in _python_files() if "updates.clear.ml" in p.read_text(encoding="utf-8")]
    assert hits == []


def test_the_update_check_module_is_gone():
    import importlib.util

    assert importlib.util.find_spec("cairnml.utilities.check_updates") is None

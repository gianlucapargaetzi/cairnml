"""The three files with the most silent broad handlers keep none: every handler that catches a broad exception logs it."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    "cairnml/backend_interface/task/repo/scriptinfo.py",
    "cairnml/binding/frameworks/tensorflow_bind.py",
    "cairnml/storage/helper.py",
]
BROAD = ("Exception", "BaseException")


def _is_silent(handler):
    broad = handler.type is None or (isinstance(handler.type, ast.Name) and handler.type.id in BROAD)
    if not broad:
        return False
    body = [n for n in handler.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    return all(
        isinstance(n, (ast.Pass, ast.Continue))
        or (isinstance(n, ast.Return) and (n.value is None or isinstance(n.value, ast.Constant)))
        for n in body
    )


def test_no_silent_broad_handler_in_the_three_files():
    offenders = []
    for name in FILES:
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_silent(node):
                offenders.append("{}:{}".format(name, node.lineno))

    assert offenders == []

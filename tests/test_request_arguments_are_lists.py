"""No generated request is built from a dict view, a set, a generator or a lazy iterator (issue #166).

The generated request classes accept a list or a tuple for list fields; a dict view such as ``d.values()`` is
rejected at run time only when the request is built, so this test finds the call sites statically.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "cairnml"
SERVICES = {path.stem for path in (ROOT / "backend_api" / "services").glob("*.py") if path.stem != "__init__"}
VIEW_METHODS = {"values", "keys", "items"}
LAZY_FUNCTIONS = {"set", "frozenset", "map", "filter", "zip", "reversed", "enumerate", "iter", "range"}


def _kind(node, names):
    """What kind of lazy or unordered value ``node`` is, or None for a list-like or unknown value."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in VIEW_METHODS and not node.args:
            return f"dict view .{func.attr}()"
        if isinstance(func, ast.Name) and func.id in LAZY_FUNCTIONS:
            return f"{func.id}()"
    if isinstance(node, (ast.Set, ast.SetComp)):
        return "set"
    if isinstance(node, ast.GeneratorExp):
        return "generator"
    if isinstance(node, ast.Name) and node.id in names:
        return f"name {node.id} assigned from {names[node.id]}"
    if isinstance(node, ast.IfExp):
        return _kind(node.body, names) or _kind(node.orelse, names)
    if isinstance(node, ast.Starred):
        return _kind(node.value, names)
    return None


def _target(call):
    """'Request' or 'service class' when the call builds a generated class, else None."""
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    module = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else None
    if name.endswith("Request"):
        return "Request"
    if module in SERVICES and name[:1].isupper():
        return "service class"
    return None


def find_offenders(source, filename="snippet.py"):
    """Return ``(line, description)`` for each generated-class call whose argument is a view, set or generator."""
    tree = ast.parse(source, filename=filename)
    offenders = set()
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            continue
        names = {}
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign):
                kind = _kind(node.value, {})
                if kind:
                    names.update({t.id: kind for t in node.targets if isinstance(t, ast.Name)})
        for node in ast.walk(scope):
            if isinstance(node, ast.Call) and _target(node):
                for arg in list(node.args) + [keyword.value for keyword in node.keywords]:
                    kind = _kind(arg, names)
                    if kind:
                        offenders.add((node.lineno, kind))
    return sorted(offenders)


@pytest.mark.parametrize(
    "snippet, expected",
    [
        ("tasks.EditHyperParamsRequest(hyperparams=props.values())", "dict view .values()"),
        ("tasks.GetAllRequest(id=d.keys())", "dict view .keys()"),
        ("tasks.GetAllRequest(id={1, 2})", "set"),
        ("tasks.GetAllRequest(id=set(ids))", "set()"),
        ("tasks.GetAllRequest(id=(x for x in ids))", "generator"),
        ("tasks.GetAllRequest(id=map(str, ids))", "map()"),
        ("tasks.GetAllRequest(id=filter(None, ids))", "filter()"),
        ("tasks.GetAllRequest(id=zip(a, b))", "zip()"),
        (
            "def f(d):\n    v = d.values()\n    return tasks.GetAllRequest(id=v)",
            "name v assigned from dict view .values()",
        ),
        ("tasks.GetAllRequest(id=ids)", None),
        ("tasks.GetAllRequest(id=list(d.values()))", None),
        ("tasks.GetAllRequest(id=ids if ok else d.values())", "dict view .values()"),
        ("tasks.GetAllRequest(id=[1, 2])", None),
    ],
)
def test_the_detector_sees_each_kind_and_passes_lists(snippet, expected):
    offenders = find_offenders(snippet)
    if expected is None:
        assert offenders == []
    else:
        assert [description for _, description in offenders] == [expected]


def test_no_generated_request_or_service_class_gets_a_view_a_set_or_a_generator():
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("backend_api/services") or "_vendor" in relative or "/external/" in relative:
            continue
        for line, description in find_offenders(path.read_text(encoding="utf-8"), filename=relative):
            offenders.append(f"cairnml/{relative}:{line} {description}")

    assert offenders == []

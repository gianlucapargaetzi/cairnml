"""Child process of the requirements scan: reads the imports of a project or one script with pigar and says where each
imported name comes from.

It runs as ``python -I <this file>`` in a new process, so importing modules to find where they live (``find_spec``)
changes nothing in the training process. It must not import ``cairnml``: a script run this way has the utilities
folder on the path only with ``-I`` not set, and the ``-I`` flag keeps it that way.

Input (JSON on standard input): ``project_root``, ``ignores`` (glob patterns), and one of ``entry`` (a script path),
``code`` (a notebook cell string, with ``file`` as its name) or neither (scan the whole project).

Output (JSON on standard output): ``imports`` as ``[name, try, file, lineno]`` rows, one per import, and ``origins``
mapping each counted name (see ``_counted_names``) to ``"std"``, ``"installed"`` (or missing), or ``{"local": path}``.
"""

import importlib.util
import json
import os
import sys


def _project_file(project_root, name):
    """The path of a module or package of the project itself, or None."""
    if not project_root or not name:
        return None
    module_file = os.path.join(project_root, name + ".py")
    if os.path.isfile(module_file):
        return module_file
    package_dir = os.path.join(project_root, name)
    if os.path.isfile(os.path.join(package_dir, "__init__.py")):
        return package_dir
    return None


def _origin(name):
    """True for the standard library, a path for a module of the environment outside site-packages, else False."""
    if name in sys.builtin_module_names:
        return True
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False
    if not spec:
        return name == "__builtin__"
    origin = spec.origin
    if not origin and spec.loader is not None:
        return False  # a subpackage
    if origin == "built-in":
        return True
    if origin is None:
        return True
    if "site-packages" in origin or "dist-packages" in origin or ("bin/" in origin and origin.endswith(".py")):
        return False
    if sys.prefix in origin or sys.base_prefix in origin or sys.base_exec_prefix in origin:
        return True
    return origin


# the names an import is counted as, the same mapping as the SDK's ImportedModules
_SPECIAL_CASES = {
    "dogpile.cache": "dogpile.cache",
    "dogpile.core": "dogpile.core",
    "ruamel.yaml": "ruamel.yaml",
    "ruamel.ordereddict": "ruamel.ordereddict",
}


def _counted_names(name):
    if not name or name.startswith("."):
        return []
    special_name = ".".join(name.split(".")[:2])
    if name.startswith("flask.ext.") and len(name.split(".")) > 2:
        return ["flask", "flask_" + name.split(".")[2]]
    if special_name in _SPECIAL_CASES:
        return [_SPECIAL_CASES[special_name]]
    if "." in name:
        return [name.split(".")[0]]
    return [name]


def _origins(names, project_root):
    origins = {}
    for name in names:
        local = _project_file(project_root, name)
        if local:
            origins[name] = {"local": local}
            continue
        origin = _origin(name)
        if origin is True:
            origins[name] = "std"
        elif origin is False:
            origins[name] = "installed"
        else:
            origins[name] = {"local": origin}
    return origins


def _imports(request):
    from pigar.parser import parse_file_imports, parse_imports

    project_root = request.get("project_root") or ""
    ignores = request.get("ignores") or []
    if request.get("code") is not None:
        found = parse_file_imports(request.get("file") or "notebook.ipynb", request["code"].encode("utf-8"))
    elif request.get("entry"):
        with open(request["entry"], "rb") as f:
            found = parse_file_imports(request["entry"], f.read())
    else:
        found, _annotations = parse_imports(project_root, exclude_patterns=ignores)
    return [[m.name, bool(m.try_), m.file, m.lineno] for m in found]


def main():
    request = json.loads(sys.stdin.read())
    rows = _imports(request)
    counted = {counted for name, *_ in rows for counted in _counted_names(name)}
    origins = _origins(sorted(counted), request.get("project_root") or "")
    json.dump({"imports": rows, "origins": origins}, sys.stdout)


if __name__ == "__main__":
    main()

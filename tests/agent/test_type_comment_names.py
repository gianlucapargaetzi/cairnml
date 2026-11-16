"""Every capitalised name a type comment (# type: ...) uses is bound in its file.

Neither pyflakes nor ruff reads type comments, so a comment can name something its file never imports or defines
(a NameError for any type checker, and a wrong hint for the reader). A name counts as bound when the file imports,
defines or assigns it anywhere, or when it is a builtin. Dotted names (a.Foo) are not checked. Vendored and generated
code is excluded like in test_unused_names.py.
"""

import ast
import builtins
import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "cairnml_agent"
EXCLUDED = ("_vendor", "/external/", "helper/gpu/pyrsmi.py", "backend_api/services")
# Files (or directories) that agent#47 and the cairnml[gpu] work replace by SDK or package imports: a fix here would
# be thrown away. The change that replaces one must remove its entry (test_the_allowlist_has_no_stale_entry).
PENDING_REPLACEMENT = ()
TYPE_COMMENT = re.compile(r"#\s*type:\s*(?!ignore\b)(?P<body>[^#]*)")
NAME = re.compile(r"(?<![\w.])[A-Z][A-Za-z0-9_]*")


def _bound_names(tree):
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
    return bound


def unbound_type_comment_names(source):
    """Return [(line, name)] for capitalised names in type comments that the source does not bind."""
    bound = _bound_names(ast.parse(source))
    findings = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        match = TYPE_COMMENT.match(token.string)
        if not match:
            continue
        for name in NAME.findall(match["body"]):
            if name not in bound and not hasattr(builtins, name):
                findings.append((token.start[0], name))
    return findings


def _checked_files():
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if any(part in relative for part in EXCLUDED):
            continue
        if any(part in relative for part in PENDING_REPLACEMENT):
            continue
        yield path, relative


def test_every_type_comment_name_is_bound():
    findings = []
    for path, relative in _checked_files():
        for line, name in unbound_type_comment_names(path.read_text(encoding="utf-8")):
            findings.append("{}:{} {}".format(relative, line, name))
    assert findings == []


def test_the_allowlist_has_no_stale_entry():
    stale = [entry for entry in PENDING_REPLACEMENT if not (PACKAGE / entry).exists()]
    assert stale == [], "remove the entry: the file is gone or replaced"


def test_an_unbound_name_is_reported():
    assert unbound_type_comment_names("def f(a):\n    # type: (Foo) -> int\n    return 1\n") == [(2, "Foo")]


def test_a_bound_or_exempt_name_passes():
    sources = [
        "import Foo\ndef f(a):\n    # type: (Foo) -> int\n    pass\n",
        "class Foo:\n    pass\n\ndef f(a):\n    # type: (Foo) -> int\n    pass\n",
        "from x import Foo\nx = 1  # type: Foo\n",
        "from x import Bar as Foo\nx = 1  # type: Foo\n",
        "Foo = int\nx = 1  # type: Foo\n",
        "x = 1  # type: int\n",
        "x = 1  # type: a.Foo\n",
        "x = 1  # type: ignore\n",
        "x = 1  # type: ignore  # Foo is not bound\n",
        "x = None  # type: Exception\n",
    ]
    for source in sources:
        assert unbound_type_comment_names(source) == [], source

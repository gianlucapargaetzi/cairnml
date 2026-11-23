"""The agent's own code has no unused import and no unused local, as pyflakes reports them.

pyflakes does not read type comments (# type: ...), and this code base uses them, so an import is also used when a type
comment in the same file names it. pyflakes ignores noqa comments, so a finding is accepted only when its source line
carries "# noqa: F401" followed by a comment that says why the import is kept. Vendored and generated code is excluded.
"""

import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = ("_vendor", "/external/", "backend_api/services")
FINDING = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+):\d+: (?P<message>.*)$")
UNUSED_IMPORT = "imported but unused"
UNUSED_LOCAL = "assigned to but never used"
ACCEPTED_IMPORT = re.compile(r"# noqa: F401\s+#\s*\S")


def _type_comment_names(source):
    names = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and re.match(r"#\s*type:", token.string):
            names.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", token.string))
    return names


def _offenders():
    try:
        import pyflakes  # noqa: F401  # the check needs it; tests/requirements.txt pins it
    except ImportError:
        pytest.fail("pyflakes is not installed: tests/requirements.txt pins it for this check")
    run = subprocess.run([sys.executable, "-m", "pyflakes", "cairnml_agent"], cwd=ROOT, capture_output=True, text=True)
    assert run.returncode in (0, 1), run.stderr
    assert run.stderr == "", run.stderr
    offenders = []
    for line in run.stdout.split("\n"):
        match = FINDING.match(line)
        if not match:
            continue
        path = Path(match["path"]).as_posix()
        message = match["message"]
        if not (UNUSED_IMPORT in message or UNUSED_LOCAL in message):
            continue
        if any(part in path for part in EXCLUDED):
            continue
        source = (ROOT / path).read_text(encoding="utf-8")
        if UNUSED_IMPORT in message:
            bound = re.search(r"'([^']+)'", message).group(1).split(" as ")[-1].split(".")[-1]
            if bound in _type_comment_names(source):
                continue
            if ACCEPTED_IMPORT.search(source.split("\n")[int(match["line"]) - 1]):
                continue
        offenders.append("{}:{} {}".format(path, match["line"], message))
    return offenders


def test_no_unused_import_or_local_in_the_agent():
    assert _offenders() == []

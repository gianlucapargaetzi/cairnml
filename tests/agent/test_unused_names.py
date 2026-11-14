"""The agent's own code has no unused import and no unused local, as pyflakes reports them.

pyflakes ignores noqa comments, so a finding is accepted here only when its source line carries "# noqa: F401" followed by
a comment that says why the import is kept. Vendored and generated code is excluded, as it was from the dead-code gate.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = ("_vendor", "/external/", "helper/gpu/pynvml.py", "helper/gpu/pyrsmi.py", "backend_api/services")
FINDING = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+):\d+: (?P<message>.*)$")
UNUSED = ("imported but unused", "assigned to but never used")
ACCEPTED_IMPORT = re.compile(r"# noqa: F401\s+#\s*\S")


def _offenders():
    try:
        import pyflakes  # noqa: F401  # the check needs it; tests/requirements.txt pins it
    except ImportError:
        pytest.fail("pyflakes is not installed: tests/requirements.txt pins it for this check")
    output = subprocess.run(
        [sys.executable, "-m", "pyflakes", "cairnml_agent"], cwd=ROOT, capture_output=True, text=True
    ).stdout
    offenders = []
    for line in output.splitlines():
        match = FINDING.match(line)
        if not match or not any(text in match["message"] for text in UNUSED):
            continue
        path = match["path"]
        if any(part in path for part in EXCLUDED):
            continue
        source = (ROOT / path).read_text(encoding="utf-8").splitlines()[int(match["line"]) - 1]
        if "imported but unused" in match["message"] and ACCEPTED_IMPORT.search(source):
            continue
        offenders.append("{}:{} {}".format(path, match["line"], match["message"]))
    return offenders


def test_no_unused_import_or_local_in_the_agent():
    assert _offenders() == []

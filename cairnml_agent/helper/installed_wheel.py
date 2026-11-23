"""A wheel rebuilt from the files of an installed distribution, for the job containers (issue #145).

A job container installs the agent and the SDK from wheels. When the agent runs from a pip install there is no source
tree to build them from, so the wheel is repacked from what the host has installed: the files listed in the
distribution's RECORD, with its own METADATA, WHEEL and entry points, and a new RECORD. The container then gets the
code the host runs, whatever installed it (PyPI, git, a wheel file, a local build) and no index is needed.
"""

import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Optional

from cairnml_agent.errors import CommandFailedError

# the files pip writes for its own bookkeeping: they describe the install, not the package
_INSTALLER_FILES = ("RECORD", "INSTALLER", "REQUESTED", "direct_url.json")


def _is_editable(dist) -> bool:
    try:
        text = dist.read_text("direct_url.json")
    except OSError:
        return False
    if not text:
        return False
    try:
        return bool(json.loads(text).get("dir_info", {}).get("editable"))
    except (ValueError, AttributeError):
        return False


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def installed_distribution(name: str, package_dir: Path):
    """The installed, non-editable distribution ``name`` when it owns the package folder ``package_dir``, else None.

    It owns the folder when the folder is inside the distribution's root (the site-packages it was installed to). A
    package imported from a source tree, or a distribution that is editable, gives None: the source tree is used then.
    """
    try:
        dist = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    if _is_editable(dist):
        return None
    root = Path(dist.locate_file("")).resolve()
    return dist if _is_inside(Path(package_dir).resolve(), root) else None


def _tag_of(wheel_text: str) -> Optional[str]:
    """The compressed tag of the wheel (py2.py3-none-any) from the Tag lines of its WHEEL file, or None."""
    tags = [line.split(":", 1)[1].strip() for line in wheel_text.splitlines() if line.startswith("Tag:")]
    if not tags:
        return None
    parts = [tag.split("-") for tag in tags]
    if any(len(part) != 3 for part in parts):
        return None
    pythons = sorted({part[0] for part in parts})
    abis = sorted({part[1] for part in parts})
    platforms = sorted({part[2] for part in parts})
    return "{}-{}-{}".format(".".join(pythons), ".".join(abis), ".".join(platforms))


def _digest(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _is_excluded(path: str, dist_info: str) -> bool:
    parts = path.split("/")
    if parts[0] == "..":  # scripts and data outside the package, in the environment's own folders
        return True
    if "__pycache__" in parts or path.endswith(".pyc"):  # compiled caches of this machine
        return True
    return parts[0] == dist_info and parts[-1] in _INSTALLER_FILES and len(parts) == 2


def wheel_from_installed(dist, out_dir) -> str:
    """Write a wheel of the installed distribution ``dist`` into ``out_dir`` and return its path.

    Raises CommandFailedError, naming the cause, when the install cannot be repacked: editable, without a RECORD,
    not pure Python, or with a file its RECORD lists missing.
    """
    name = dist.metadata["Name"]
    if _is_editable(dist):
        raise CommandFailedError("{} is an editable install: it is not repacked, its source tree is used".format(name))
    record = dist.read_text("RECORD")
    if record is None:
        raise CommandFailedError("{} has no RECORD file: cannot list its installed files".format(name))
    wheel_text = dist.read_text("WHEEL") or ""
    if "Root-Is-Purelib: true" not in wheel_text:
        raise CommandFailedError(
            "{} is not pure Python (its WHEEL file does not say Root-Is-Purelib: true): "
            "the job containers cannot use a copy of it".format(name)
        )
    tag = _tag_of(wheel_text)
    if tag is None:
        raise CommandFailedError("{} has no Tag line in its WHEEL file".format(name))

    wheel_name = re.sub(r"[-_.]+", "_", name)
    dist_info = "{}-{}.dist-info".format(wheel_name, dist.version)

    files = {}
    for row in csv.reader(io.StringIO(record)):
        if not row or not row[0] or _is_excluded(row[0], dist_info):
            continue
        source = Path(dist.locate_file(row[0]))
        if not source.is_file():
            raise CommandFailedError("{}: a file listed in its RECORD is missing: {}".format(name, row[0]))
        files[row[0]] = source.read_bytes()

    out = Path(out_dir) / "{}-{}-{}.whl".format(wheel_name, dist.version, tag)
    rows = [(path, _digest(data), str(len(data))) for path, data in sorted(files.items())]
    rows.append(("{}/RECORD".format(dist_info), "", ""))
    record_buf = io.StringIO()
    csv.writer(record_buf, lineterminator="\n").writerows(rows)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in sorted(files.items()):
            archive.writestr(path, data)
        archive.writestr("{}/RECORD".format(dist_info), record_buf.getvalue())
    return str(out)

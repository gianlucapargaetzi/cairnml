"""A wheel rebuilt from an installed distribution (issue #145): the container gets the files the host runs.

The installed distribution is a fake one in a temporary folder, read by importlib.metadata like a pip install."""

import base64
import csv
import hashlib
import io
import subprocess
import sys
import zipfile
from importlib.metadata import PathDistribution
from pathlib import Path

import pytest

from cairnml_agent.errors import CommandFailedError
from cairnml_agent.helper.installed_wheel import wheel_from_installed

NAME = "fake_pkg"
VERSION = "1.2.3"
DIST_INFO = "{}-{}.dist-info".format(NAME, VERSION)


def digest(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def install(
    site: Path, files: dict, wheel_text: str = "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
):
    """Write a fake installed distribution (files and dist-info, with a RECORD that lists them) under ``site``."""
    dist_info = site / DIST_INFO
    all_files = dict(files)
    all_files[DIST_INFO + "/METADATA"] = "Metadata-Version: 2.1\nName: fake-pkg\nVersion: {}\n".format(VERSION)
    all_files[DIST_INFO + "/WHEEL"] = wheel_text
    all_files[DIST_INFO + "/entry_points.txt"] = "[console_scripts]\nfake = fake_pkg:main\n"
    all_files[DIST_INFO + "/INSTALLER"] = "pip\n"
    all_files[DIST_INFO + "/REQUESTED"] = ""
    # what pip leaves next to the code: a compiled cache, and a script in the environment's bin folder
    all_files[NAME + "/__pycache__/__init__.cpython-312.pyc"] = b"\0\0compiled"
    for rel, text in all_files.items():
        path = site / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    rows = []
    for rel in all_files:
        data = (site / rel).read_bytes()
        rows.append((rel, digest(data), str(len(data))))
    rows.append(("../../bin/fake", digest(b"#!python\n"), "9"))
    rows.append((DIST_INFO + "/RECORD", "", ""))
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    (dist_info / "RECORD").write_text(buf.getvalue())
    return PathDistribution(dist_info)


@pytest.fixture
def fake_site(tmp_path):
    site = tmp_path / "site-packages"
    site.mkdir()
    return site


def record_rows(wheel: Path):
    with zipfile.ZipFile(wheel) as archive:
        text = archive.read(DIST_INFO + "/RECORD").decode("utf-8")
    return list(csv.reader(io.StringIO(text)))


def test_the_wheel_has_the_installed_files_and_installs(fake_site, tmp_path):
    dist = install(
        fake_site,
        {NAME + "/__init__.py": "def main():\n    return 0\n", NAME + "/data.txt": "payload"},
    )
    out = tmp_path / "out"
    out.mkdir()

    wheel = Path(wheel_from_installed(dist, out))

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert names == {
            NAME + "/__init__.py",
            NAME + "/data.txt",
            DIST_INFO + "/METADATA",
            DIST_INFO + "/WHEEL",
            DIST_INFO + "/entry_points.txt",
            DIST_INFO + "/RECORD",
        }
        assert archive.read(NAME + "/data.txt") == b"payload"

    # the installed file set, minus the installer's own bookkeeping and the compiled cache
    assert not any(n.startswith("__pycache__") or n.endswith(".pyc") for n in names)
    assert not any(n.endswith(("INSTALLER", "REQUESTED")) for n in names)

    target = tmp_path / "target"
    done = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--no-index", "--target", str(target), str(wheel)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr
    assert (target / NAME / "data.txt").read_text() == "payload"


def test_the_record_of_the_new_wheel_has_a_correct_hash_and_size_for_every_file(fake_site, tmp_path):
    dist = install(fake_site, {NAME + "/__init__.py": "x = 1\n"})
    out = tmp_path / "out"
    out.mkdir()
    wheel = Path(wheel_from_installed(dist, out))

    rows = record_rows(wheel)
    with zipfile.ZipFile(wheel) as archive:
        for path, hash_text, size in rows:
            if path == DIST_INFO + "/RECORD":
                assert hash_text == "" and size == ""
                continue
            data = archive.read(path)
            assert hash_text == digest(data), path
            assert size == str(len(data)), path


def test_the_name_and_tag_come_from_the_installed_wheel_file(fake_site, tmp_path):
    dist = install(
        fake_site,
        {NAME + "/__init__.py": "x = 1\n"},
        wheel_text="Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\nTag: py2-none-any\n",
    )
    out = tmp_path / "out"
    out.mkdir()
    wheel = Path(wheel_from_installed(dist, out))
    assert wheel.name == "fake_pkg-1.2.3-py2.py3-none-any.whl"


def test_an_editable_install_cannot_be_repacked(fake_site, tmp_path):
    dist = install(fake_site, {NAME + "/__init__.py": "x = 1\n"})
    (fake_site / DIST_INFO / "direct_url.json").write_text(
        '{"url": "file:///src/fake", "dir_info": {"editable": true}}'
    )
    with pytest.raises(CommandFailedError, match="editable.*fake-pkg|fake-pkg.*editable"):
        wheel_from_installed(dist, tmp_path)


def test_an_install_without_a_record_cannot_be_repacked(fake_site, tmp_path):
    dist = install(fake_site, {NAME + "/__init__.py": "x = 1\n"})
    (fake_site / DIST_INFO / "RECORD").unlink()
    with pytest.raises(CommandFailedError, match="RECORD.*fake-pkg|fake-pkg.*RECORD"):
        wheel_from_installed(dist, tmp_path)


def test_a_compiled_install_cannot_be_repacked(fake_site, tmp_path):
    dist = install(
        fake_site,
        {NAME + "/__init__.py": "x = 1\n"},
        wheel_text="Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: cp312-cp312-linux_x86_64\n",
    )
    with pytest.raises(CommandFailedError, match="not pure Python.*fake-pkg|fake-pkg.*not pure"):
        wheel_from_installed(dist, tmp_path)


def test_a_record_entry_whose_file_is_missing_cannot_be_repacked(fake_site, tmp_path):
    dist = install(fake_site, {NAME + "/__init__.py": "x = 1\n"})
    (fake_site / NAME / "__init__.py").unlink()
    with pytest.raises(CommandFailedError, match="missing.*__init__.py|__init__.py.*missing"):
        wheel_from_installed(dist, tmp_path)

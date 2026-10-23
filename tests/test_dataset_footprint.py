"""The footprint of a dataset repository: what makes it verifiable from git alone."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from clearml.datasets import footprint as fp

DVC = "outs:\n- md5: 465a62c55506cd2fa10372c1f9ba4b5e.dir\n  size: 2048\n  nfiles: 3\n  hash: md5\n  path: data\n"


def lock_for(dvc_text=DVC, config="format: native\n", samples=3, **changes):
    fmt = fp.config_format(fp.parse_config(config))
    if fmt["kind"] == "adapter":
        fmt = dict(fmt, registry_commit="a" * 40)
    body = fp.build_lock(fp.parse_dvc_file(dvc_text), fmt, samples, {"image": "image"}, {"class": "class"}, "2.1.13")
    body.update(changes)
    return body


def files(lock=None, dvc=DVC, config="format: native\n"):
    lock = lock if lock is not None else lock_for(dvc, config)
    return {fp.DVC_FILE: dvc.encode(), fp.CONFIG_FILE: config.encode(), fp.LOCK_FILE: json.dumps(lock).encode()}


def test_a_matching_lock_passes():
    assert fp.verify_files(files(), {"data.dvc": 100, "dataset.yaml": 20, "dataset.lock.json": 300}) == []


def test_missing_files_say_what_to_run():
    problems = fp.verify_files({fp.DVC_FILE: None, fp.CONFIG_FILE: b"format: native", fp.LOCK_FILE: None}, {})
    assert any("data.dvc is missing" in p and "add" in p for p in problems)
    assert any("dataset.lock.json is missing" in p and "commit" in p for p in problems)


def test_data_that_changed_after_the_check_is_found():
    changed = DVC.replace("2048", "4096")
    problems = fp.verify_files(files(lock_for(), dvc=changed), {})
    assert any("other data" in p for p in problems)


def test_a_hand_edit_without_the_checksum_is_found():
    lock = lock_for()
    lock["samples"] = 99
    assert any("checksum" in p for p in fp.verify_files(files(lock), {}))


def test_a_hand_edit_with_a_recomputed_checksum_gets_through():
    """This is a footprint, not a signature: a matching lock written by hand passes. Using the data catches it."""
    lock = lock_for()
    lock["samples"] = 99
    lock["footprint"] = fp.checksum(lock)
    assert fp.verify_files(files(lock), {}) == []


def test_a_lock_for_another_format_than_the_config_asks_for_is_found():
    lock = lock_for(config="adapter: coco\n")
    problems = fp.verify_files(files(lock, config="format: native\n"), {})
    assert any("asks for" in p for p in problems)


def test_an_adapter_dataset_needs_the_registry_commit():
    lock = lock_for(config="adapter: coco\n")
    lock["format"] = {"kind": "adapter", "name": "coco"}
    lock["footprint"] = fp.checksum(lock)
    assert any("registry" in p for p in fp.verify_files(files(lock, config="adapter: coco\n"), {}))


def test_a_lock_with_no_samples_is_refused():
    lock = lock_for(samples=0)
    lock["footprint"] = fp.checksum(lock)
    assert any("no samples" in p for p in fp.verify_files(files(lock), {}))


def test_data_files_in_git_and_large_files_are_found():
    problems = fp.verify_files(files(), {"data/a.csv": 10, "notes.bin": 5, "docs/big.pdf": 3 * 1024 * 1024})
    assert any("data/a.csv" in p for p in problems)
    assert any("notes.bin" in p for p in problems)
    assert any("docs/big.pdf" in p for p in problems)


def test_a_config_must_pick_one_format():
    with pytest.raises(ValueError):
        fp.config_format(fp.parse_config("format: native\nadapter: coco\n"))
    with pytest.raises(ValueError):
        fp.config_format(fp.parse_config("registry: x\n"))


def test_the_script_runs_alone_and_exits_non_zero_on_problems(tmp_path):
    """The copy in .clearml/verify.py is run by a GitHub Action with no SDK installed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / fp.DVC_FILE).write_text(DVC)
    (repo / fp.CONFIG_FILE).write_text("format: native\n")
    (repo / fp.LOCK_FILE).write_text(json.dumps(lock_for()))
    script = tmp_path / "verify.py"
    script.write_text(Path(fp.__file__).read_text())
    good = subprocess.run([sys.executable, "-I", str(script), str(repo)], capture_output=True, text=True)
    assert good.returncode == 0, good.stderr
    (repo / "data.dvc").write_text(DVC.replace("2048", "1"))
    bad = subprocess.run([sys.executable, "-I", str(script), str(repo)], capture_output=True, text=True)
    assert bad.returncode == 1 and "other data" in bad.stderr


def test_the_checksum_of_a_fixed_lock_is_the_same_in_the_sdk_and_on_the_server():
    """The server holds a copy of this module to verify a dataset before it is registered. Both suites pin this value."""
    assert lock_for()["footprint"] == "275fe1c61757148538b8b2c964aac9c60daca857b04b8fce4e0dbf037118d2f4"

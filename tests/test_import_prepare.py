import zipfile
from pathlib import Path

import pytest
import yaml

from cairnml.cli.import_run import __main__ as prepare_cli

RUN = "yolo_yolo26n_20260519_120037_480x640px_birdeye_grayscale"


def make_run(root: Path) -> Path:
    run = root / "work" / RUN
    (run / "weights").mkdir(parents=True)
    (run / "weights" / "best.pt").write_bytes(b"weights")
    (run / "results.csv").write_text("epoch,loss\n0,1.0\n")
    (run / "args.yaml").write_text("lr: 0.01\n")
    (run / "__pycache__").mkdir()
    (run / "__pycache__" / "x.pyc").write_bytes(b"x")
    (root / "work" / "train.py").write_text("print('train')\n")
    return run


def test_the_run_name_timestamp_and_resolution_come_from_the_folder_name():
    parsed = prepare_cli.parse_run_name(RUN)
    assert parsed == {"name": "yolo_yolo26n", "timestamp": "2026-05-19 12:00:37", "resolution": "480x640px"}


def test_a_folder_name_in_another_form_keeps_its_name():
    assert prepare_cli.parse_run_name("my-run")["name"] == "my-run"


def test_the_training_script_is_found_by_walking_up_and_the_nearest_one_wins(tmp_path):
    run = make_run(tmp_path)
    assert prepare_cli.find_train_script(run) == tmp_path / "work" / "train.py"
    (run / "train.py").write_text("print('nearer')\n")
    assert prepare_cli.find_train_script(run) == run / "train.py"


def test_the_zip_holds_the_run_with_a_manifest_and_no_cache(tmp_path):
    run = make_run(tmp_path)
    output = tmp_path / "out" / f"{RUN}.zip"

    warnings = prepare_cli.prepare(run, output, project="examples", tags=["lab"])

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        manifest = yaml.safe_load(archive.read(f"{RUN}/cairnml-import.yaml"))
    assert {f"{RUN}/weights/best.pt", f"{RUN}/results.csv", f"{RUN}/args.yaml"} <= names
    assert not any("__pycache__" in name for name in names)
    assert manifest["project"] == "examples"
    assert manifest["task"] == "yolo_yolo26n"
    assert manifest["tags"] == ["lab"]
    assert "2026-05-19 12:00:37" in manifest["comment"] and "480x640px" in manifest["comment"]
    assert set(manifest) <= {"format", "project", "task", "comment", "tags"}
    assert warnings == []


def test_the_run_folder_is_not_written_to(tmp_path):
    run = make_run(tmp_path)
    before = sorted(p.relative_to(run).as_posix() for p in run.rglob("*"))
    prepare_cli.prepare(run, tmp_path / "out.zip", project="examples")
    assert sorted(p.relative_to(run).as_posix() for p in run.rglob("*")) == before


def test_a_missing_project_is_a_warning_and_the_zip_is_still_written(tmp_path):
    run = make_run(tmp_path)
    warnings = prepare_cli.prepare(run, tmp_path / "out.zip")
    assert any("project is empty" in w for w in warnings)
    assert (tmp_path / "out.zip").is_file()


def test_the_zip_is_not_written_inside_the_run_folder(tmp_path):
    run = make_run(tmp_path)
    with pytest.raises(SystemExit):
        prepare_cli.prepare(run, run / "inside.zip")


def test_an_existing_zip_is_kept_unless_forced(tmp_path):
    run = make_run(tmp_path)
    target = tmp_path / "out.zip"
    target.write_bytes(b"old")
    with pytest.raises(SystemExit):
        prepare_cli.prepare(run, target)
    assert target.read_bytes() == b"old"
    prepare_cli.prepare(run, target, force=True)
    assert zipfile.is_zipfile(target)


def test_the_temporary_folder_is_removed_when_packaging_fails(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(prepare_cli.tempfile, "mkdtemp", lambda prefix: str(scratch))

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(prepare_cli.zipfile.ZipFile, "write", broken)
    with pytest.raises(OSError):
        prepare_cli.prepare(run, tmp_path / "out.zip")
    assert not scratch.exists()
    assert not (tmp_path / "out.zip").exists()


def test_the_command_line_writes_the_zip_and_reports_the_path(tmp_path, capsys):
    run = make_run(tmp_path)
    output = tmp_path / "cli.zip"
    assert prepare_cli.main(["prepare", str(run), "--project", "examples", "--task", "run-a", "--output", str(output)]) == 0
    assert "wrote" in capsys.readouterr().out
    with zipfile.ZipFile(output) as archive:
        assert yaml.safe_load(archive.read(f"{RUN}/cairnml-import.yaml"))["task"] == "run-a"

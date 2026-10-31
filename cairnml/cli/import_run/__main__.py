"""Prepare a training run folder for import into CairnML, as one ZIP file:

    cairnml import prepare <run-folder> [--project NAME] [--task NAME] [--comment TEXT] [--tags a,b] [--output FILE] [--force]

The ZIP holds the run folder as one top-level folder, with a manifest (cairnml-import.yaml) written next to the files.
The manifest's project and task are required by the server; prepare fills the task from the folder name and leaves the
project empty unless --project is given, and it warns about what is missing. Prepare does not run the training script,
and it never writes into the run folder: it stages the manifest and the ZIP in a temporary folder that is removed on
success and on failure.

The run is uploaded and imported with the CairnML web app (Import run) or the imports API.
"""
import argparse
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional

import yaml

MANIFEST_NAME = "cairnml-import.yaml"
TRAIN_SCRIPT = "train.py"
MANIFEST_FORMAT = 1
RECOMMENDED = ("args.yaml", "results.csv")
SKIP_DIRS = {"__pycache__"}

# yolo_yolo26n_20260519_120037_480x640px_birdeye_grayscale: the run name, the start stamp and the input resolution.
_RUN_NAME = re.compile(r"^(?P<name>.+?)_(?P<stamp>\d{8}_\d{6})_(?P<resolution>\d+x\d+px)")


def parse_run_name(folder_name: str) -> dict:
    """The run name, timestamp and resolution from a folder name in the usual form. Missing parts are None."""
    match = _RUN_NAME.match(folder_name)
    if not match:
        return {"name": folder_name, "timestamp": None, "resolution": None}
    stamp = match.group("stamp")
    timestamp = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}"
    return {"name": match.group("name"), "timestamp": timestamp, "resolution": match.group("resolution")}


def find_train_script(start: Path) -> Optional[Path]:
    """The nearest train.py at or above start: the training script of the run, or None."""
    for folder in [start, *start.parents]:
        candidate = folder / TRAIN_SCRIPT
        if candidate.is_file():
            return candidate
    return None


def manifest_for(run_dir: Path, project: str = "", task: str = "", comment: str = "", tags: Optional[List[str]] = None) -> dict:
    """The manifest fields the server accepts: format, project, task, comment and tags."""
    parsed = parse_run_name(run_dir.name)
    details = [part for part in (
        f"started {parsed['timestamp']}" if parsed["timestamp"] else "",
        f"input {parsed['resolution']}" if parsed["resolution"] else "",
    ) if part]
    text = comment or ""
    if details:
        text = (text + " " if text else "") + f"({'; '.join(details)})"
    manifest = {
        "format": MANIFEST_FORMAT,
        "project": project or "",
        "task": task or parsed["name"],
    }
    if text:
        manifest["comment"] = text
    if tags:
        manifest["tags"] = list(tags)
    return manifest


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
    except ValueError:
        return False
    return True


def _files(run_dir: Path):
    for path in sorted(run_dir.rglob("*")):
        if path.is_dir() or any(part in SKIP_DIRS for part in path.relative_to(run_dir).parts):
            continue
        if path.name == MANIFEST_NAME and path.parent == run_dir:
            continue  # the manifest is written by prepare, never copied from the folder
        yield path


def prepare(run_dir: Path, output: Path, project: str = "", task: str = "", comment: str = "",
            tags: Optional[List[str]] = None, force: bool = False) -> List[str]:
    """Writes the ZIP at output. Returns the warnings about what is still to fill in or missing."""
    run_dir = run_dir.resolve()
    if not run_dir.is_dir():
        raise SystemExit(f"cairnml import prepare: not a folder: {run_dir}")
    output = output.resolve()
    if output.exists() and not force:
        raise SystemExit(f"cairnml import prepare: {output} exists (use --force to replace it)")
    if _inside(output, run_dir):
        raise SystemExit("cairnml import prepare: the ZIP must not be written inside the run folder")

    warnings = []
    if not project:
        warnings.append("project is empty: set it with --project or in the manifest before the import")
    if find_train_script(run_dir) is None:
        warnings.append(f"no {TRAIN_SCRIPT} found at or above the run folder")
    for name in RECOMMENDED:
        if not (run_dir / name).is_file():
            warnings.append(f"no {name}: it is not imported")
    if not (run_dir / "weights").is_dir():
        warnings.append("no weights/ folder: no checkpoints are imported")

    scratch = Path(tempfile.mkdtemp(prefix="cairnml-import-"))
    try:
        root = run_dir.name
        manifest_path = scratch / root / MANIFEST_NAME
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(yaml.safe_dump(manifest_for(run_dir, project, task, comment, tags), sort_keys=False))
        staged = scratch / f"{root}.zip"
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(manifest_path, f"{root}/{MANIFEST_NAME}")
            for path in _files(run_dir):
                archive.write(path, f"{root}/{path.relative_to(run_dir).as_posix()}")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged), str(output))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return warnings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cairnml import prepare", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="write the run folder as a ZIP with its manifest")
    p.add_argument("run_dir", type=Path, help="the run folder")
    p.add_argument("--project", default="", help="the project to import into (required before the import)")
    p.add_argument("--task", default="", help="the task name for the new task (default: the run name)")
    p.add_argument("--comment", default="", help="a comment on the run")
    p.add_argument("--tags", default="", help="comma-separated tags")
    p.add_argument("--output", type=Path, help="the ZIP to write (default: <run name>.zip in this folder)")
    p.add_argument("--force", action="store_true", help="replace an existing ZIP")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    run_dir = args.run_dir.resolve()
    output = args.output or Path.cwd() / f"{run_dir.name}.zip"
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    warnings = prepare(run_dir, output, project=args.project, task=args.task, comment=args.comment,
                       tags=tags, force=args.force)
    print(f"wrote {output}")
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

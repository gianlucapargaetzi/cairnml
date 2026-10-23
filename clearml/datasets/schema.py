"""The canonical shape of an external dataset after its adapter has read it.

One row per sample: an id, a split, the files of each modality, and the labels. Modalities are named
files (for example ``image`` and ``depth`` of a stereo set); labels are named and typed (for example
``class``, ``bbox2d``). A training pipeline reads this shape only, whatever the source looked like.

Box format, always: ``[x1, y1, x2, y2]`` in pixels of the image, with x2 > x1 and y2 > y1.
"""

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import DatasetError

SCHEMA_VERSION = 1
SAMPLES_FILE = "samples.parquet"
MANIFEST_FILE = "manifest.json"

MODALITY_TYPES = ("image", "depth", "audio", "video", "text", "table", "file")
LABEL_TYPES = ("class", "bbox2d", "mask2d", "json")


class SchemaError(DatasetError):
    pass


def _pa():
    try:
        import pyarrow as pa
    except ImportError:
        raise DatasetError("pyarrow is not installed: pip install 'clearml[dataset]'") from None
    return pa


def arrow_type(label_type: str):
    pa = _pa()
    if label_type in ("class", "mask2d", "json"):
        return pa.string()
    if label_type == "bbox2d":
        return pa.list_(pa.struct([("box", pa.list_(pa.float32())), ("label", pa.string())]))
    raise SchemaError(f"unknown label type {label_type!r}; known: {', '.join(LABEL_TYPES)}")


def modality_column(name: str) -> str:
    return f"modality.{name}"


def label_column(name: str) -> str:
    return f"label.{name}"


def check_spec(modalities: Dict[str, str], labels: Dict[str, str]) -> None:
    if not modalities:
        raise SchemaError("an adapter must declare at least one modality")
    for name, kind in modalities.items():
        if kind not in MODALITY_TYPES:
            raise SchemaError(f"modality {name!r} has unknown type {kind!r}; known: {', '.join(MODALITY_TYPES)}")
    for name, kind in labels.items():
        if kind not in LABEL_TYPES:
            raise SchemaError(f"label {name!r} has unknown type {kind!r}; known: {', '.join(LABEL_TYPES)}")


def _check_bbox2d(sample_id: str, name: str, value: Any) -> List[str]:
    problems = []
    for index, item in enumerate(value or []):
        box = item.get("box") if isinstance(item, dict) else None
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            problems.append(f"{sample_id}: {name}[{index}] needs a box of 4 numbers [x1, y1, x2, y2]")
            continue
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box):
            problems.append(f"{sample_id}: {name}[{index}] box has values that are not finite numbers")
        elif box[2] <= box[0] or box[3] <= box[1]:
            problems.append(
                f"{sample_id}: {name}[{index}] box {list(box)} is not [x1, y1, x2, y2] with x2 > x1 and y2 > y1 "
                f"(pixels, not [x, y, width, height])"
            )
        if not isinstance(item.get("label"), str) or not item["label"]:
            problems.append(f"{sample_id}: {name}[{index}] has no label name")
    return problems


def validate_rows(
    rows: List[Dict[str, Any]],
    modalities: Dict[str, str],
    labels: Dict[str, str],
    root: Optional[Path] = None,
    limit: int = 20,
) -> None:
    """Raise a SchemaError listing what is wrong (at most ``limit`` problems)."""
    problems: List[str] = []
    seen = set()
    for row in rows:
        sample_id = row.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            problems.append(f"a sample has no id: {row.get('sample_id')!r}")
            continue
        if sample_id in seen:
            problems.append(f"sample id {sample_id!r} appears twice")
        seen.add(sample_id)
        if not isinstance(row.get("split", ""), str):
            problems.append(f"{sample_id}: split must be text")
        for name in modalities:
            value = row.get(modality_column(name))
            if not isinstance(value, str) or not value:
                problems.append(f"{sample_id}: modality {name!r} has no file")
            elif root is not None and "://" not in value and not (root / value).exists():
                problems.append(f"{sample_id}: modality {name!r} file {value!r} does not exist in the source")
        for name, kind in labels.items():
            value = row.get(label_column(name))
            if kind == "bbox2d":
                problems.extend(_check_bbox2d(sample_id, name, value))
            elif kind == "class" and value is not None and not isinstance(value, str):
                problems.append(f"{sample_id}: label {name!r} must be a class name (text)")
        if len(problems) >= limit:
            break
    if problems:
        shown = "\n  ".join(problems[:limit])
        raise SchemaError(f"the adapter's output is not in the canonical format:\n  {shown}")


def write_manifest(directory: Path, modalities: Dict[str, str], labels: Dict[str, str], extra: Dict[str, Any]) -> None:
    manifest = {"schema_version": SCHEMA_VERSION, "modalities": modalities, "labels": labels}
    manifest.update(extra)
    (directory / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2, sort_keys=True))


def read_manifest(directory: Path) -> Dict[str, Any]:
    try:
        return json.loads((directory / MANIFEST_FILE).read_text())
    except (OSError, ValueError) as exc:
        raise DatasetError(f"no readable {MANIFEST_FILE} in {directory}: {exc}") from None

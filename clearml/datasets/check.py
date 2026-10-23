"""The check of a dataset repository: read ``data/`` in the format the dataset declares, and validate what comes out.

A dataset is either **native**, which means ``data/`` already holds the standard sample table
(``samples.parquet`` and ``manifest.json``, see :mod:`clearml.datasets.schema`), or it names an **adapter** from the
registry that reads its files into that table. Anything else cannot be checked, so it cannot be published.
"""

import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from . import footprint
from .adapter import SampleWriter
from .cache import DatasetCache
from .errors import DatasetError
from .external import _default_repo, load_adapter, registry_tree
from .schema import MANIFEST_FILE, SAMPLES_FILE, SchemaError, read_manifest, validate_rows


def run_check(data_dir: Path, config: Mapping[str, str], cache: Optional[DatasetCache] = None) -> Dict[str, Any]:
    """Read ``data_dir`` as ``config`` says and validate it.

    :return: ``{"format", "samples", "modalities", "labels"}`` for the lock file
    :raises DatasetError: with a message that says what to fix
    """
    try:
        wanted = footprint.config_format(config)
    except ValueError as exc:
        raise DatasetError(str(exc)) from None
    if wanted["kind"] == "native":
        return _check_native(data_dir)
    return _check_adapter(data_dir, wanted, config, cache or DatasetCache.default())


def _validate_table(table_dir: Path, source_root: Path, who: str) -> Dict[str, Any]:
    import pyarrow.parquet as pq

    manifest = read_manifest(table_dir)
    rows = pq.read_table(table_dir / SAMPLES_FILE).to_pylist()
    if not rows:
        raise DatasetError(f"{who}: the sample table is empty")
    try:
        validate_rows(rows, manifest["modalities"], manifest["labels"], root=source_root)
    except SchemaError as exc:
        raise DatasetError(f"{who}: {exc}") from None
    return {"samples": len(rows), "modalities": manifest["modalities"], "labels": manifest["labels"]}


def _check_native(data_dir: Path) -> Dict[str, Any]:
    missing = [name for name in (SAMPLES_FILE, MANIFEST_FILE) if not (data_dir / name).is_file()]
    if missing:
        raise DatasetError(
            f"data/ is declared native but has no {' and '.join(missing)}. Put the standard sample table there, or name "
            f"an adapter in dataset.yaml (adapter: <name>) that reads these files into it."
        )
    result = _validate_table(data_dir, data_dir, "native dataset")
    return {"format": {"kind": "native"}, **result}


def _check_adapter(data_dir: Path, wanted: Mapping[str, str], config: Mapping[str, str], cache: DatasetCache):
    registry = wanted.get("registry") or _default_repo()
    tree, sha = registry_tree(cache, registry, config.get("registry_commit") or None)
    name = wanted["name"]
    adapter = load_adapter(tree, name)
    with tempfile.TemporaryDirectory() as out:
        out_dir = Path(out)
        writer = SampleWriter(out_dir, dict(adapter.modalities), dict(adapter.labels), source_root=data_dir)
        try:
            adapter.run(data_dir, writer)
            writer.close()
        except SchemaError as exc:
            raise DatasetError(f"adapter {name!r}: {exc}") from None
        except DatasetError:
            raise
        except Exception as exc:
            raise DatasetError(
                f"adapter {name!r} could not read data/: {type(exc).__name__}: {exc}. Fix the data or choose another "
                f"adapter in dataset.yaml."
            ) from exc
        result = _validate_table(out_dir, data_dir, f"adapter {name!r}")
    return {"format": {"kind": "adapter", "name": name, "registry": registry, "registry_commit": sha}, **result}

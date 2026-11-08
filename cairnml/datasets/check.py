"""The check of a dataset repository: read ``data/`` in the format the dataset declares, and validate what comes out.

A dataset is either **native**, which means ``data/`` already holds the standard sample table
(``samples.parquet`` and ``manifest.json``, see :mod:`cairnml.datasets.schema`), or it names an **adapter** that reads
its files into that table. The adapter is either one from the registry (``adapter: <name>``) or ``adapter.py`` at the top
of the repository (``adapter: local``). Anything else cannot be checked, so it cannot be published.
"""

import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from . import footprint
from .adapter import SampleWriter
from .cache import DatasetCache
from .errors import DatasetError
from .external import (
    _default_repo,
    adapter_requirements,
    check_requirements,
    load_adapter_module,
    load_local_adapter,
    registry_tree,
)
from .schema import MANIFEST_FILE, SAMPLES_FILE, SchemaError, from_arrow_value, label_column, read_manifest, validate_rows


def run_check(data_dir: Path, config: Mapping[str, str], cache: Optional[DatasetCache] = None) -> Dict[str, Any]:
    """Read ``data_dir`` as ``config`` says and validate it. The repository is the folder above ``data_dir``.

    :return: ``{"format", "samples", "modalities", "labels"}`` for the lock file
    :raises DatasetError: with a message that says what to fix
    """
    try:
        wanted = footprint.config_format(config)
    except ValueError as exc:
        raise DatasetError(str(exc)) from None
    if wanted["kind"] == "native":
        return _check_native(data_dir)
    return _check_adapter(data_dir, wanted, config, cache)


def _validate_table(table_dir: Path, source_root: Path, who: str) -> Dict[str, Any]:
    import pyarrow.parquet as pq

    manifest = read_manifest(table_dir)
    rows = pq.read_table(table_dir / SAMPLES_FILE).to_pylist()
    if not rows:
        raise DatasetError(f"{who}: the sample table is empty")
    try:
        for row in rows:  # back to the form the adapter writes: a tabular label is a dict, not a list of cells
            for name, kind in manifest["labels"].items():
                if label_column(name) in row:
                    row[label_column(name)] = from_arrow_value(kind, row[label_column(name)])
        validate_rows(rows, manifest["modalities"], manifest["labels"], root=source_root)
    except SchemaError as exc:
        raise DatasetError(f"{who}: {exc}") from None
    splits: Dict[str, int] = {}
    for row in rows:
        splits[row["split"]] = splits.get(row["split"], 0) + 1
    return {"samples": len(rows), "modalities": manifest["modalities"], "labels": manifest["labels"], "splits": splits}


def _check_native(data_dir: Path) -> Dict[str, Any]:
    missing = [name for name in (SAMPLES_FILE, MANIFEST_FILE) if not (data_dir / name).is_file()]
    if missing:
        raise DatasetError(
            f"data/ is declared native but has no {' and '.join(missing)}. Put the standard sample table there, or name "
            f"an adapter in dataset.yaml (adapter: <name>, or adapter: {footprint.LOCAL_ADAPTER} with "
            f"{footprint.ADAPTER_FILE} at the top of the repository) that reads these files into it."
        )
    result = _validate_table(data_dir, data_dir, "native dataset")
    return {"format": {"kind": "native"}, **result}


def _check_adapter(data_dir: Path, wanted: Mapping[str, str], config: Mapping[str, str], cache: Optional[DatasetCache]):
    if wanted["kind"] == "local":  # no registry and no cache: the configuration is not needed at all
        # The adapter is code of this repository: it runs here, on the machine that checks the data.
        path = data_dir.parent / footprint.ADAPTER_FILE
        label = f"local adapter '{footprint.ADAPTER_FILE}'"
        digest = footprint.sha256_bytes(path.read_bytes()) if path.is_file() else None
        adapter = load_local_adapter(path)
        fmt = {"kind": "local", "name": footprint.LOCAL_ADAPTER, "adapter_sha256": digest}
    else:
        registry = wanted.get("registry") or _default_repo()
        tree, sha = registry_tree(cache or DatasetCache.default(), registry, config.get("registry_commit") or None)
        name = wanted["name"]
        adapter, module = load_adapter_module(tree, name)
        check_requirements(adapter_requirements(module, name), name)
        label = f"adapter {name!r}"
        fmt = {"kind": "adapter", "name": name, "registry": registry, "registry_commit": sha}
    with tempfile.TemporaryDirectory() as out:
        out_dir = Path(out)
        writer = SampleWriter(out_dir, dict(adapter.modalities), dict(adapter.labels), source_root=data_dir)
        try:
            adapter.run(data_dir, writer)
            writer.close()
        except SchemaError as exc:
            raise DatasetError(f"{label}: {exc}") from None
        except DatasetError:
            raise
        except Exception as exc:
            fix = "Fix the data or the adapter." if wanted["kind"] == "local" else (
                "Fix the data or choose another adapter in dataset.yaml.")
            raise DatasetError(f"{label} could not read data/: {type(exc).__name__}: {exc}. {fix}") from exc
        result = _validate_table(out_dir, data_dir, label)
    return {"format": fmt, **result}

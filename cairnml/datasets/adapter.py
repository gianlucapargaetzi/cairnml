"""Adapters turn the files of an outside dataset into the canonical format.

An adapter is a small class in the ``external-datasets`` repository::

    from cairnml.datasets import Adapter

    class CocoDetection(Adapter):
        modalities = {"image": "image"}
        labels = {"bbox2d": "bbox2d"}

        def run(self, source, writer):
            for item in read_source_somehow(source):
                writer.add(item.id, item.split, {"image": item.file}, {"bbox2d": item.boxes})

``source`` is the folder with the files of the dataset at the revision the run asked for. The writer checks
every sample against the canonical format; a layout the adapter does not expect should raise an error that
names the adapter, so the run stops before training.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from .schema import (
    SAMPLES_FILE,
    SchemaError,
    _pa,
    arrow_type,
    check_spec,
    label_column,
    modality_column,
    validate_rows,
    write_manifest,
)


class SampleWriter:
    def __init__(self, out: Path, modalities: Dict[str, str], labels: Dict[str, str], source_root: Optional[Path] = None):
        check_spec(modalities, labels)
        self.out = out
        self.modalities = modalities
        self.labels = labels
        self.source_root = source_root
        self._rows: List[Dict[str, Any]] = []

    def add(
        self,
        sample_id: str,
        split: str,
        modalities: Dict[str, str],
        labels: Optional[Dict[str, Any]] = None,
    ) -> None:
        """One sample. File paths are relative to the source folder (or a URI)."""
        unknown = set(modalities) - set(self.modalities)
        if unknown:
            raise SchemaError(f"{sample_id}: modalities {sorted(unknown)} were not declared by the adapter")
        row: Dict[str, Any] = {"sample_id": sample_id, "split": split or ""}
        for name in self.modalities:
            row[modality_column(name)] = modalities.get(name)
        for name in self.labels:
            row[label_column(name)] = (labels or {}).get(name)
        self._rows.append(row)

    def close(self, extra: Optional[Dict[str, Any]] = None) -> int:
        """Check the samples and write the table and manifest. Returns the number of samples."""
        validate_rows(self._rows, self.modalities, self.labels, root=self.source_root)
        pa = _pa()
        import pyarrow.parquet as pq

        columns = [("sample_id", pa.string()), ("split", pa.string())]
        columns += [(modality_column(n), pa.string()) for n in self.modalities]
        columns += [(label_column(n), arrow_type(k)) for n, k in self.labels.items()]
        table = pa.Table.from_pylist(self._rows, schema=pa.schema(columns))
        pq.write_table(table, self.out / SAMPLES_FILE)
        write_manifest(self.out, self.modalities, self.labels, extra or {})
        return len(self._rows)


class Adapter:
    """Base class of adapters. Set ``modalities`` and ``labels`` and implement ``run``."""

    modalities: Dict[str, str] = {}
    labels: Dict[str, str] = {}

    def run(self, source: Path, writer: SampleWriter) -> None:
        raise NotImplementedError

    @classmethod
    def adapter_name(cls) -> str:
        return cls.__module__.rsplit(".", 1)[-1]

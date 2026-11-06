"""Where a dataset's files are, when they are on this machine.

A registry entry with ``source: local`` names a folder that is used where it is: its revision is a hash of what is in
it. Every other source is downloaded by its registry adapter (``fetch``), and its revision is resolved by the adapter
(``resolve``), so this module does not know about any outside service.
"""

import hashlib
from pathlib import Path
from typing import Optional

from .errors import DatasetError


class LocalFolder:
    """A folder with no revision history. The revision is a hash of what is in it."""

    kind = "local"
    downloads = False

    def local_root(self, link: str) -> Path:
        path = Path(link.replace("file://", "", 1)).expanduser().resolve()
        if not path.is_dir():
            raise DatasetError(f"{path} is not a folder")
        return path

    def resolve(self, link: str, revision: Optional[str]) -> str:
        root = self.local_root(link)
        digest = hashlib.sha256()
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            digest.update(str(path.relative_to(root)).encode())
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(block)
        found = digest.hexdigest()
        if revision and revision != found:
            raise DatasetError(f"{root} no longer matches the requested content hash {revision[:12]}")
        return found

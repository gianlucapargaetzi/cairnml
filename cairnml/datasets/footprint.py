"""The footprint of a dataset repository: a record that a dataset was checked, which anyone can verify from git alone.

``cairnml dataset check`` validates the data and writes ``dataset.lock.json`` next to ``data.dvc``. The lock copies the
size, file count and content hash that DVC wrote to ``data.dvc``, says in which format the data was read (native, or
through an adapter), and ends with a checksum of everything above it. ``verify`` recomputes what it can from files that
are in git, so it needs no data, no server and no key:

- ``data.dvc`` and the lock agree, so the data has not changed since the check. A dataset with ``storage: lfs`` has no
  ``data.dvc``: its lock records the files of ``data/`` as the LFS objects their pointer files name (see
  :func:`lfs_record`), and ``verify`` recomputes that record from the pointers
- the checksum matches the lock's content, so the lock was not edited by hand without recomputing it
- ``dataset.yaml`` and the lock name the same format
- the repository holds only the files a dataset repository may hold, none of them large

It is not a signature. Someone who writes a matching lock by hand gets past it; that fails when the dataset is used.

This file uses only the standard library and does not import the rest of the SDK, because ``cairnml dataset init``
copies it to ``.cairnml/verify.py`` in each dataset repository, where a GitHub Action runs it as a script.
"""

import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple

FOOTPRINT_VERSION = 1
LOCK_FILE = "dataset.lock.json"
CONFIG_FILE = "dataset.yaml"
DVC_FILE = "data.dvc"
ATTRIBUTES_FILE = ".gitattributes"
VERIFY_SCRIPT = ".cairnml/verify.py"
MAX_TRACKED_BYTES = 1024 * 1024
POINTER_MAX_BYTES = 1024  # an LFS pointer file is about 130 bytes; a larger file in git was not stored in LFS
STORAGES = ("s3", "lfs")  # s3: the files are in DVC's bucket; lfs: in the GitHub LFS of the dataset repository
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1\n"
LFS_PATTERN = "data/** filter=lfs diff=lfs merge=lfs -text"

# What may be tracked by git at the top of a dataset repository. The data itself is in DVC or in LFS.
ALLOWED_TOP_LEVEL = {
    DVC_FILE, CONFIG_FILE, LOCK_FILE, ".gitignore", ".dvcignore", ".dvc", ".github", ".cairnml", ATTRIBUTES_FILE,
    "README.md", "README", "LICENSE", "LICENSE.md", "NOTICE", "docs",
}

_SHA = re.compile(r"[0-9a-f]{7,40}")


# Reading the two small text files, without a YAML library


def parse_dvc_file(text: str) -> Dict[str, Any]:
    """``hash``, ``size`` and ``nfiles`` of the first output in a ``data.dvc`` file."""
    found: Dict[str, Any] = {}
    for key in ("md5", "size", "nfiles"):
        match = re.search(rf"^\s*-?\s*{key}:\s*(\S+)\s*$", text, re.M)
        if match:
            found[key] = match.group(1)
    if "md5" not in found or "size" not in found or "nfiles" not in found:
        raise ValueError("data.dvc has no md5, size and nfiles for its output")
    return {"hash": found["md5"], "size": int(found["size"]), "nfiles": int(found["nfiles"])}


def parse_config(text: str) -> Dict[str, str]:
    """``key: value`` lines of ``dataset.yaml``. Comments and blank lines are ignored."""
    config: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            raise ValueError(f"dataset.yaml: cannot read the line {line!r}")
        key, value = line.split(":", 1)
        config[key.strip()] = value.strip().strip("'\"")
    return config


def storage_kind(config: Mapping[str, str]) -> str:
    """Where the files of ``data/`` live: ``s3`` (DVC, the default) or ``lfs`` (GitHub LFS of the repository)."""
    value = config.get("storage", "s3")
    if value not in STORAGES:
        raise ValueError(f"dataset.yaml says storage: {value!r}; use storage: s3 or storage: lfs")
    return value


def config_format(config: Mapping[str, str]) -> Dict[str, str]:
    """The format ``dataset.yaml`` asks for: ``{"kind": "native"}`` or ``{"kind": "adapter", "name": ..., ...}``."""
    kind, adapter = config.get("format"), config.get("adapter")
    if adapter and kind not in (None, "adapter"):
        raise ValueError("dataset.yaml sets both 'format: native' and an adapter; use one of them")
    if adapter:
        result = {"kind": "adapter", "name": adapter}
        if config.get("registry"):
            result["registry"] = config["registry"]
        return result
    if kind == "native":
        return {"kind": "native"}
    raise ValueError("dataset.yaml must say 'format: native' or name an 'adapter: <name>'")


# The footprint


def checksum(body: Mapping[str, Any]) -> str:
    """sha256 of the lock's content, as canonical JSON, without its own ``footprint`` field."""
    content = {key: value for key, value in body.items() if key != "footprint"}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_lock(
    dvc: Optional[Mapping[str, Any]],
    fmt: Mapping[str, str],
    samples: int,
    modalities: Mapping[str, str],
    labels: Mapping[str, str],
    tool_version: str,
    lfs: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """The lock. The content is ``dvc`` (from data.dvc) for s3 storage, or ``lfs`` (see :func:`lfs_record`) for lfs."""
    body: Dict[str, Any] = {
        "version": FOOTPRINT_VERSION,
        "format": dict(fmt),
        "samples": int(samples),
        "modalities": dict(modalities),
        "labels": dict(labels),
        "tool_version": tool_version,
    }
    if lfs is not None:
        body["lfs"] = dict(lfs)
    else:
        body["dvc"] = dict(dvc or {})
    body["footprint"] = checksum(body)
    return body


def _lfs_object(path: str) -> Tuple[str, int]:
    """The sha256 and size of one file of data/. A pointer file gives the object it names; any other file is hashed."""
    size = os.path.getsize(path)
    if size < POINTER_MAX_BYTES:
        with open(path, "rb") as handle:
            head = handle.read()
        if head.startswith(LFS_POINTER):
            oid = re.search(rb"^oid sha256:([0-9a-f]{64})$", head, re.M)
            length = re.search(rb"^size (\d+)$", head, re.M)
            if not oid or not length:
                raise ValueError(f"{path} is an LFS pointer without an oid and a size")
            return oid.group(1).decode(), int(length.group(1))
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest(), size


def lfs_record(root: str) -> Dict[str, Any]:
    """The content of ``data/`` for a dataset in LFS: ``hash`` (sha256 of the sorted list of path, object and size),
    ``size`` (bytes) and ``nfiles``. The files may be pointers (a checkout without ``git lfs pull``) or their content;
    both give the same record.
    """
    base = os.path.join(root, "data")
    rows = []
    for folder, dirs, names in os.walk(base):
        dirs.sort()
        for name in names:
            path = os.path.join(folder, name)
            oid, size = _lfs_object(path)
            rows.append((os.path.relpath(path, base).replace(os.sep, "/"), oid, size))
    rows.sort()
    listing = "".join(f"{path}\t{oid}\t{size}\n" for path, oid, size in rows)
    return {
        "hash": hashlib.sha256(listing.encode()).hexdigest(),
        "size": sum(size for _p, _o, size in rows),
        "nfiles": len(rows),
    }


def _blank(files: Mapping[str, Optional[bytes]], name: str) -> bool:
    return files.get(name) is None


def uses_lfs(files: Mapping[str, Optional[bytes]]) -> bool:
    """True when ``dataset.yaml`` (in ``files``) says the data is stored in LFS."""
    if _blank(files, CONFIG_FILE):
        return False
    try:
        return storage_kind(parse_config(files[CONFIG_FILE].decode())) == "lfs"
    except (ValueError, UnicodeDecodeError):
        return False


def verify_files(
    files: Mapping[str, Optional[bytes]],
    sizes: Mapping[str, int],
    lfs: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """What is wrong with a dataset repository, as sentences that say what to do. Empty when it is fine.

    :param files: the content of ``data.dvc``, ``dataset.yaml`` and ``dataset.lock.json`` (None when missing)
    :param sizes: size in bytes of every file tracked by git, by path
    :param lfs: for a dataset in LFS, the :func:`lfs_record` of ``data/`` (None when it could not be read)
    """
    problems: List[str] = []
    if _blank(files, CONFIG_FILE):
        problems.append(f"{CONFIG_FILE} is missing: run 'cairnml dataset init'")
    if _blank(files, LOCK_FILE):
        problems.append(f"{LOCK_FILE} is missing: run 'cairnml dataset commit' to check the data and write it")
    if not _blank(files, CONFIG_FILE) and not uses_lfs(files) and _blank(files, DVC_FILE):
        problems.append(f"{DVC_FILE} is missing: run 'cairnml dataset add'")
    if problems:
        return problems
    try:
        config = parse_config(files[CONFIG_FILE].decode())
        storage = storage_kind(config)
    except (ValueError, UnicodeDecodeError) as exc:
        return [f"cannot read the dataset files: {exc}"]

    try:
        dvc = parse_dvc_file(files[DVC_FILE].decode()) if storage == "s3" else None
        wanted = config_format(config)
        lock = json.loads(files[LOCK_FILE].decode())
    except (ValueError, UnicodeDecodeError) as exc:
        return [f"cannot read the dataset files: {exc}"]
    if not isinstance(lock, dict):
        return [f"{LOCK_FILE} is not a JSON object"]

    if lock.get("version") != FOOTPRINT_VERSION:
        problems.append(f"{LOCK_FILE} has version {lock.get('version')!r}; this tool reads {FOOTPRINT_VERSION}")
    if lock.get("footprint") != checksum(lock):
        problems.append(f"the checksum in {LOCK_FILE} does not match its content: it was edited; run 'cairnml dataset check'")
    if storage == "s3" and lock.get("dvc") != dvc:
        problems.append(
            f"{LOCK_FILE} was written for other data than {DVC_FILE} holds "
            f"(lock: {lock.get('dvc')}, data.dvc: {dvc}): the data changed after the check; run 'cairnml dataset commit'"
        )
    if storage == "lfs":
        if lfs is None:
            problems.append("the files in data/ could not be read as LFS objects; run 'cairnml dataset add'")
        elif lock.get("lfs") != dict(lfs):
            problems.append(
                f"{LOCK_FILE} was written for other data than data/ holds (lock: {lock.get('lfs')}, data/: {dict(lfs)}): "
                f"the data changed after the check; run 'cairnml dataset commit'"
            )
    fmt = lock.get("format") or {}
    if fmt.get("kind") != wanted.get("kind") or fmt.get("name") != wanted.get("name"):
        problems.append(f"{LOCK_FILE} was made for format {fmt} but {CONFIG_FILE} asks for {wanted}; run 'cairnml dataset check'")
    if fmt.get("kind") == "adapter" and not _SHA.fullmatch(str(fmt.get("registry_commit", ""))):
        problems.append(f"{LOCK_FILE} does not say which commit of the adapter registry was used")
    if not isinstance(lock.get("samples"), int) or lock["samples"] < 1:
        problems.append(f"{LOCK_FILE} reports no samples: the data was not read into the standard format")

    for path, size in sorted(sizes.items()):
        top = path.split("/", 1)[0]
        if top == "data" and storage == "lfs":
            continue  # the files of data/ are LFS pointers, checked through the record above
        if top not in ALLOWED_TOP_LEVEL:
            problems.append(f"{path} is tracked by git; only the dataset files may be (put data in data/, which DVC tracks)")
        elif top == "data":
            problems.append(f"{path} is tracked by git; data/ belongs to DVC")
        elif size > MAX_TRACKED_BYTES:
            problems.append(f"{path} is {size} bytes in git; large files belong in data/")
    return problems


def tracked_sizes(root: str) -> Dict[str, int]:
    """Size of every file under ``root``, without the git folder and the DVC content store, by relative path."""
    sizes: Dict[str, int] = {}
    skip = {".git", ".venv", "__pycache__", "data"}
    for folder, dirs, names in os.walk(root):
        rel = os.path.relpath(folder, root)
        dirs[:] = [d for d in dirs if not (rel == "." and d in skip)
                   and not (rel.startswith(".dvc") and d in ("cache", "tmp"))]
        if rel.startswith(".dvc"):
            continue
        for name in names:
            if rel == "." and name in (".ready", ".last_used"):  # markers of the dataset cache, not of the repository
                continue
            path = os.path.normpath(os.path.join(rel, name)).replace(os.sep, "/")
            sizes[path] = os.path.getsize(os.path.join(folder, name))
    return sizes


def read_files(root: str) -> Dict[str, Optional[bytes]]:
    files: Dict[str, Optional[bytes]] = {}
    for name in (DVC_FILE, CONFIG_FILE, LOCK_FILE):
        path = os.path.join(root, name)
        files[name] = open(path, "rb").read() if os.path.isfile(path) else None
    return files


def verify(root: str = ".") -> List[str]:
    """Verify the dataset repository in a folder. ``root`` is a checkout of the repository, not of its data."""
    sizes = {p: s for p, s in tracked_sizes(root).items() if not p.startswith(".dvc/")}
    files = read_files(root)
    if not uses_lfs(files):
        return verify_files(files, sizes)
    try:
        record = lfs_record(root)
    except ValueError as exc:
        return [str(exc)]
    return verify_files(files, sizes, record)


def main(argv: Optional[List[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = args[0] if args else "."
    problems = verify(root)
    if problems:
        print("This dataset repository does not pass its checks:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("The dataset footprint is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

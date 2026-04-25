import hashlib
import json
from typing import Optional, Union, Dict, Tuple
from zlib import crc32

from clearml.debugging.log import LoggerRoot

STR_HASH_FUNCTIONS = {"md5", "sha256", "sha384", "sha512"}
DICT_HASH_FUNCTIONS = {"crc32", *STR_HASH_FUNCTIONS}
StrHashFunction = str  # Literal["md5", "sha256", "sha384", "sha512"]
DictHashFunction = str  # Literal["md5", "sha256", "sha384", "sha512", "crc32"]


def sha256sum(
    filename: str,
    skip_header: int = 0,
    block_size: int = 65536,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Computes SHA-256 hashes for a file, with an option to skip a header section.

    This function generates two hashes: one for the body of the file (excluding
    the header) and one for the entire file (if a header is skipped). Using a
    memoryview and a pre-allocated bytearray ensures high performance and
    low memory overhead when processing large files.

    :param str filename: The path to the file to be hashed.
    :param Optional[int] skip_header: The number of bytes at the start of the file to treat as a header.
        If non-zero, the function tracks the full file hash separately from the body hash.
    :param Optional[int] block_size: The size of the buffer (in bytes) used to read the file.
        Defaults to 65536 (64 KB).
    :return: A tuple containing the hex digest of the file body (excluding
        the header) and the hex digest of the full file. The second element
        is None if skip_header is 0. Returns (None, None) on error.
    """
    h = hashlib.sha256()
    file_hash = hashlib.sha256()
    b = bytearray(block_size)
    mv = memoryview(b)
    try:
        with open(filename, "rb", buffering=0) as f:
            if skip_header:
                file_hash.update(f.read(skip_header))
            # noinspection PyUnresolvedReferences
            for n in iter(lambda: f.readinto(mv), 0):
                h.update(mv[:n])
                if skip_header:
                    file_hash.update(mv[:n])
    except Exception as e:
        LoggerRoot.get_base_logger().warning(str(e))
        return None, None

    return (
        h.hexdigest(),
        (
            file_hash.hexdigest()
            if skip_header
            else None
        )
    )


def crc32text(
    text: str,
    seed: Union[int, str] = 1337,
) -> str:
    """
    Return crc32 hash of a string
    Do not use this hash for security, if needed use something stronger like SHA2

    :param text: string to hash
    :param seed: use prefix seed for hashing
    :return: crc32 hex in string (32bits = 8 characters in hex)
    """
    hash = crc32(f"{seed}{text}".encode("utf-8"))
    return f"{hash:08x}"


def hash_text(
    text: str,
    seed: Union[int, str] = 1337,
    hash_func: StrHashFunction = "md5",
) -> str:
    """
    Return hash_func (md5/sha1/sha256/sha384/sha512) hash of a string

    :param text: string to hash
    :param seed: use prefix seed for hashing
    :param hash_func: hashing function. currently supported md5 sha256
    :return: hashed string
    """
    if hash_func not in STR_HASH_FUNCTIONS:
        raise ValueError(f"Invalid hash function: '{hash_func}'. Valid hash_func parameters are: {STR_HASH_FUNCTIONS}")
    h = getattr(hashlib, hash_func)()
    h.update((str(seed) + str(text)).encode("utf-8"))
    return h.hexdigest()


def md5text(
    text: str,
    seed: Union[int, str] = 1337,
) -> str:
    """
    Return md5 hash of a string
    Do not use this hash for security, if needed use something stronger like SHA2

    :param text: string to hash
    :param seed: use prefix seed for hashing
    :return: md5 string
    """
    return hash_text(text=text, seed=seed, hash_func="md5")


def hash_dict(
    a_dict: Dict,
    seed: Union[int, str] = 1337,
    hash_func: DictHashFunction = "md5",
) -> str:
    """
    Return hash_func (crc32/md5/sha1/sha256/sha384/sha512) hash of the dict values
    (dict must be JSON serializable)

    :param a_dict: a dictionary to hash
    :param seed: use prefix seed for hashing
    :param hash_func: hashing function. currently supported md5 sha256
    :return: hashed string
    """
    if hash_func not in DICT_HASH_FUNCTIONS:
        raise ValueError(f"Invalid hash function: '{hash_func}'. Valid hash_func parameters are: {DICT_HASH_FUNCTIONS}")
    repr_string = json.dumps(a_dict, sort_keys=True)
    if hash_func == "crc32":
        return crc32text(text=repr_string, seed=seed)
    else:
        return hash_text(text=repr_string, seed=seed, hash_func=hash_func)

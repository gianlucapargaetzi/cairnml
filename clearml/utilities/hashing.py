import hashlib
import sys

StrHashFunction = str  # Literal["md5", "sha256", "sha384", "sha512"]

# ``usedforsecurity`` was added to hashlib constructors in Python 3.9.
# All hashing in this SDK is non-cryptographic
# (caching, deduplication, identity, path shortening),
# so we pass ``usedforsecurity=False`` where supported.
# This lets these digests (notably md5) run on FIPS-hardened runtimes,
# where the crypto provider otherwise refuses to instantiate them.
IS_HASHLIB_USEDFORSECURITY_SUPPORTED = sys.version_info >= (3, 9)


def safe_hash(
    name: StrHashFunction,
    data: bytes = b"",
    *args,
    **kwargs,
):
    """
    Create a hashlib hasher object marked as non-security-related so it works in FIPS-compliant systems.

    :param name: hash algorithm name (e.g. "md5", "sha256")
    :param data: optional initial data to hash
    :return: a hashlib hash object
    """
    return hashlib.new(
        name,
        data,
        *args,
        **kwargs,
        **(
            {"usedforsecurity": False}  # This option silences FIPS-related errors
            if IS_HASHLIB_USEDFORSECURITY_SUPPORTED
            else {}
        ),
    )


def md5_safe_hash(
    data: bytes = b"",
    *args,
    **kwargs,
):
    """
    Create a hashlib.md5 hasher object marked as non-security-related so it works in FIPS-compliant systems.

    :param data: optional initial data to hash
    :return: a hashlib hash object
    """
    return safe_hash(
        name="md5",
        data=data,
        *args,
        **kwargs,
    )


def sha256_safe_hash(
    data: bytes = b"",
    *args,
    **kwargs,
):
    """
    Create a hashlib.sha256 hasher object marked as non-security-related so it works in FIPS-compliant systems.

    :param data: optional initial data to hash
    :return: a hashlib hash object
    """
    return safe_hash(
        name="sha256",
        data=data,
        *args,
        **kwargs,
    )


def sha384_safe_hash(
    data: bytes = b"",
    *args,
    **kwargs,
):
    """
    Create a hashlib.sha384 hasher object marked as non-security-related so it works in FIPS-compliant systems.

    :param data: optional initial data to hash
    :return: a hashlib hash object
    """
    return safe_hash(
        name="sha384",
        data=data,
        *args,
        **kwargs,
    )


def sha512_safe_hash(
    data: bytes = b"",
    *args,
    **kwargs,
):
    """
    Create a hashlib.sha512 hasher object marked as non-security-related so it works in FIPS-compliant systems.

    :param data: optional initial data to hash
    :return: a hashlib hash object
    """
    return safe_hash(
        name="sha512",
        data=data,
        *args,
        **kwargs,
    )

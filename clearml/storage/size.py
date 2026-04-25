import re
import math
from enum import Enum
from typing import Union, Tuple, Dict, Set

FORMATTING_BASES = {
    "binary": 1024,
    "decimal": 1000,
}
BINARY_SCALE: Tuple[str] = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
DECIMAL_SCALE: Tuple[str] = ("B", "KB", "MB", "GB", "TB", "PB")


def format_size(
    size_in_bytes: Union[int, float],
    binary: bool = False,
    use_nonbinary_notation: bool = False,
    use_b_instead_of_bytes: bool = False,
) -> str:
    """
    Return the size in human-readable format (string)
    Matching humanfriendly.format_size outputs

    :param size_in_bytes: number of bytes
    :param binary: If `True` 1 Kb equals 1024 bytes, if False (default) 1 KB = 1000 bytes
    :param use_nonbinary_notation: Only applies if binary is `True`. If this is `True`,
        the binary scale (KiB, MiB etc.) will be replaced with the regular scale (KB, MB etc.)
    :param use_b_instead_of_bytes: If `True`, return the formatted size with `B` as the
        scale instead of `byte(s)` (when applicable)
    :return: string representation of the number of bytes (b,Kb,Mb,Gb, Tb,)
        >>> format_size(0)
        '0 bytes'
        >>> format_size(1)
        '1 byte'
        >>> format_size(5)
        '5 bytes'
        > format_size(1000)
        '1 KB'
        > format_size(1024, binary=True)
        '1 KiB'
        >>> format_size(1000 ** 3 * 4)
        '4 GB'
    """
    numeric_system = "binary" if binary else "decimal"
    base = FORMATTING_BASES[numeric_system]

    # Compute numeric label
    size = float(size_in_bytes)
    order_of_magnitude = (
        min(int(math.log(size, base)), 5)  # 5 is for petabytes
        if size > 0  # log of zero is an error
        else 0
    )
    scaled_size = size / base**order_of_magnitude
    numeric_label = f"{scaled_size:.2f}".rstrip("0").rstrip(".")

    # Determine unit scale
    unit_scale = [*(
        DECIMAL_SCALE
        if (not binary or use_nonbinary_notation)
        else BINARY_SCALE
    )]
    if not use_b_instead_of_bytes:
        unit_scale[0] = "bytes"

    default_return_value = f"{numeric_label} {unit_scale[order_of_magnitude]}"
    # Edge case 1:
    # 999995 B gets rounded to 1000 KB by the .2f, but that should be 1 MB
    # We don't move one scale up if the scale is PB (e.g. 1000 PB instead of 1 EB)
    if (
        scaled_size < 1000
        and numeric_label == "1000"
        and order_of_magnitude < 5
    ):
        return f"1 {unit_scale[order_of_magnitude + 1]}"
    # Edge case 2: "1 bytes" should render as "1 byte"
    elif default_return_value == "1 bytes":
        return "1 byte"
    # General case
    else:
        return default_return_value


class UnitMagnitude(Enum):
    """Maps orders of magnitude to power factors."""
    BYTE = 0
    KILO = 1
    MEGA = 2
    GIGA = 3
    TERA = 4
    PETA = 5


magnitude_map: Dict[str, UnitMagnitude] = {
    "b": UnitMagnitude.BYTE, "byte": UnitMagnitude.BYTE,
    "kb": UnitMagnitude.KILO, "kilobyte": UnitMagnitude.KILO, "kib": UnitMagnitude.KILO, "kibibyte": UnitMagnitude.KILO,
    "mb": UnitMagnitude.MEGA, "megabyte": UnitMagnitude.MEGA, "mib": UnitMagnitude.MEGA, "mebibyte": UnitMagnitude.MEGA,
    "gb": UnitMagnitude.GIGA, "gigabyte": UnitMagnitude.GIGA, "gib": UnitMagnitude.GIGA, "gibibyte": UnitMagnitude.GIGA,
    "tb": UnitMagnitude.TERA, "terabyte": UnitMagnitude.TERA, "tib": UnitMagnitude.TERA, "tebibyte": UnitMagnitude.TERA,
    "pb": UnitMagnitude.PETA, "petabyte": UnitMagnitude.PETA, "pib": UnitMagnitude.PETA, "pebibyte": UnitMagnitude.PETA,
}

explicitly_binary_labels: Set[str] = {
    "kib", "kibibyte",
    "mib", "mebibyte",
    "gib", "gibibyte",
    "tib", "tebibyte",
    "pib", "pebibyte",
}


def parse_size(
    size: Union[str, float, int],
    binary: bool = False,
) -> int:
    """
    Parse a human-readable data size and return the number of bytes.
    Match humanfriendly.parse_size

    :param size: The human-readable file size to parse (a string).
    :param binary: :data:`True` to use binary multiples of bytes (base-2) for
                   ambiguous unit symbols and names, :data:`False` to use
                   decimal multiples of bytes (base-10).
    :returns: The corresponding size in bytes (an integer).
    :raises: :exc:`InvalidSize` when the input can't be parsed.

    This function knows how to parse sizes in bytes, kilobytes, megabytes,
    gigabytes, terabytes and petabytes. Some examples:
        >>> parse_size('42')
        42
        >>> parse_size('13b')
        13
        >>> parse_size('5 bytes')
        5
        >>> parse_size('1 KB')
        1000
        >>> parse_size('1 kilobyte')
        1000
        >>> parse_size('1 KiB')
        1024
        >>> parse_size('1 KB', binary=True)
        1024
        >>> parse_size('1.5 GB')
        1500000000
        >>> parse_size('1.5 GB', binary=True)
        1610612736
    """
    number, label = _tokenize_size(size=size)

    # Normalize label (strip plural 's')
    if len(label) > 1 and label.endswith("s"):
        label = label[:-1]

    if label not in magnitude_map:
        # This handles the 'K', 'M', 'G' cases and other unknowns
        raise ValueError(f"Ambiguous or unknown label: '{size}'")

    magnitude = magnitude_map[label]
    is_explicitly_binary = label in explicitly_binary_labels

    base_factor = (
        1024
        if (is_explicitly_binary or binary)
        else 1000
    ) ** magnitude.value

    return int(number * base_factor)


def _tokenize_size(size: str) -> Tuple[float, str]:
    pattern = r"^(?P<number>\d+(?:\.\d+)?)\s*(?P<label>[a-zA-Z]+)?$"
    text_input = str(size).strip()
    match = re.match(pattern, text_input)

    if not match:
        raise ValueError(f"Failed to parse size! (input '{size}' format invalid)")

    data = match.groupdict()
    number = float(data["number"])
    label = (data["label"] or "b").strip().lower()
    return (number, label)

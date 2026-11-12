"""A plain text table for the lists the command line prints (cairnml dataset list, cairnml repository list)."""

from typing import Iterable, Sequence


def table(headers: Sequence[str], rows: Iterable[Sequence]) -> str:
    """The headers and rows as aligned columns, two spaces apart, with no trailing spaces."""
    lines = [tuple(headers), *[tuple("" if value is None else str(value) for value in row) for row in rows]]
    widths = [max(len(line[i]) for line in lines) for i in range(len(headers))]
    return "\n".join("  ".join(value.ljust(width) for value, width in zip(line, widths)).rstrip() for line in lines)

import os
from itertools import takewhile
from typing import Optional, Union, Sequence

from pathlib import Path


def get_common_path(filepaths: Sequence[Union[str, Path]]) -> Optional[str]:
    """
    Return the common path of a list of file paths

    :param filepaths: list of files (str or Path objects)
    :return: Common path string (always absolute) or None if common path could not be found
    """
    if len(filepaths) == 0:
        return None

    abs_paths = [
        Path(filepath).absolute().parent
        for filepath in filepaths
    ]

    # Example: if filepaths = ["/a/b", "/c/d/e"], then `folder_names_by_depth = (("/", "/"), ("a","c"), ("b", "d"))`
    # We don't need "e" in this example since it can't be in common with other paths that don't go that deep
    folder_names_by_depth = zip(*(
        path.parts
        for path in abs_paths
    ))

    common_parts = [
        level_parts[0]  # Folder name in the first path (since they're all equal)
        for level_parts in takewhile(
            lambda folder_names: len(set(folder_names)) == 1,
            folder_names_by_depth,
        )
    ]

    if len(common_parts) == 0:
        return None

    return Path(*common_parts).as_posix()


def is_within_directory(directory: str, target: str) -> bool:
    """
    Checks if the 'target' path (formatted as a str) is within the suggested directory (also a str).
    Converts paths to absolute paths by prefixing relative paths with the output of os.getcwd()
    (via `os.path.abspath`), so that relative paths can be compared
    on equal terms with other paths.

    Examples:
        is_within_directory("a", "a/b.txt") == True
        is_within_directory("", "a/b.txt") == True
        is_within_directory("a/b", "a/b/c/d.txt") == True
        is_within_directory("a", "a.txt") == False # 'directory' variable refers to folder, not file
        is_within_directory("a/b/c", "a/b/cd") == False # sibling directories with related names don't work
        is_within_directory("a/b/cd", "a/b/ce") == False # sibling directories with related names don't work
        is_within_directory("a/b/c/e", "a/b/cd") == False # sibling directories don't work

    :param str target: Path to folder/file to check for containment in directory.
    :param str directory: Path to folder to check for containment of target file/folder.
    """
    directory_absolute_path = Path(os.path.abspath(directory))
    target_absolute_path = Path(os.path.abspath(target))

    return (
        len(target_absolute_path.parts) >= len(directory_absolute_path.parts)
        and
        directory_absolute_path.parts == (
            target_absolute_path.parts[:len(directory_absolute_path.parts)]
        )
    )

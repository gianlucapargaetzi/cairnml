import fnmatch
import os
import sys
from typing import Optional, Union, Sequence, Callable, Any

from pathlib2 import Path
from six.moves.urllib.parse import quote, urlparse, urlunparse

from ..debugging.log import LoggerRoot


def get_config_object_matcher(**patterns: Any) -> Callable:
    unsupported = {k: v for k, v in patterns.items() if not isinstance(v, str)}
    if unsupported:
        raise ValueError(
            "Unsupported object matcher (expecting string): %s"
            % ", ".join("%s=%s" % (k, v) for k, v in unsupported.items())
        )

    # optimize simple patters
    starts_with = {k: v.rstrip("*") for k, v in patterns.items() if "*" not in v.rstrip("*") and "?" not in v}
    patterns = {k: v for k, v in patterns.items() if v not in starts_with}

    def _matcher(**kwargs: Any) -> Optional[bool]:
        for key, value in kwargs.items():
            if not value:
                continue
            start = starts_with.get(key)
            if start:
                if value.startswith(start):
                    return True
            else:
                pat = patterns.get(key)
                if pat and fnmatch.fnmatch(value, pat):
                    return True

    return _matcher


def quote_url(url: str, valid_schemes: Sequence[str] = ("http", "https")) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in valid_schemes:
        return url
    parsed = parsed._replace(path=quote(parsed.path))
    return str(urlunparse(parsed))


def encode_string_to_filename(text: str) -> str:
    return quote(text, safe=" ")


def is_windows() -> bool:
    """
    :return: True if currently running on windows OS
    """
    return sys.platform == "win32"


def get_common_path(list_of_files: Sequence[Union[str, Path]]) -> Optional[str]:
    """
    Return the common path of a list of files

    :param list_of_files: list of files (str or Path objects)
    :return: Common path string (always absolute) or None if common path could not be found
    """
    if not list_of_files:
        return None

    # a single file has its parent as common path
    if len(list_of_files) == 1:
        return Path(list_of_files[0]).absolute().parent.as_posix()

    # find common path to support folder structure inside zip
    common_path_parts = Path(list_of_files[0]).absolute().parts
    for f in list_of_files:
        f_parts = Path(f).absolute().parts
        num_p = min(len(f_parts), len(common_path_parts))
        if f_parts[:num_p] == common_path_parts[:num_p]:
            common_path_parts = common_path_parts[:num_p]
            continue
        num_p = min([i for i, (a, b) in enumerate(zip(common_path_parts[:num_p], f_parts[:num_p])) if a != b] or [-1])
        # no common path, break
        if num_p < 0:
            common_path_parts = []
            break
        # update common path
        common_path_parts = common_path_parts[:num_p]

    if common_path_parts:
        common_path = Path()
        for f in common_path_parts:
            common_path /= f
        return common_path.as_posix()

    return None


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


def create_zip_directories(zipfile: Any, path: Optional[Union[str, os.PathLike]] = None) -> None:
    try:
        path = os.getcwd() if path is None else os.fspath(path)
        for member in zipfile.namelist():
            arcname = member.replace("/", os.path.sep)
            if os.path.altsep:
                arcname = arcname.replace(os.path.altsep, os.path.sep)
            # interpret absolute pathname as relative, remove drive letter or
            # UNC path, redundant separators, "." and ".." components.
            arcname = os.path.splitdrive(arcname)[1]
            invalid_path_parts = ("", os.path.curdir, os.path.pardir)
            arcname = os.path.sep.join(x for x in arcname.split(os.path.sep) if x not in invalid_path_parts)
            if os.path.sep == "\\":
                # noinspection PyBroadException
                try:
                    # filter illegal characters on Windows
                    # noinspection PyProtectedMember
                    arcname = zipfile._sanitize_windows_name(arcname, os.path.sep)
                except Exception:
                    pass

            targetpath = os.path.normpath(os.path.join(path, arcname))

            # Create all upper directories if necessary.
            upperdirs = os.path.dirname(targetpath)
            if upperdirs:
                os.makedirs(upperdirs, exist_ok=True)
    except Exception as e:
        LoggerRoot.get_base_logger().warning("Failed creating zip directories: " + str(e))


def safe_extract(
    tar: Any,
    path: str = ".",
    members: Any = None,
    numeric_owner: bool = False,
) -> None:
    """Tarfile member sanitization (addresses CVE-2007-4559)"""
    base_dir = os.path.abspath(path)
    for member in tar.getmembers():
        member_path = os.path.abspath(os.path.join(base_dir, member.name))
        if not is_within_directory(base_dir, member_path):
            raise Exception("Path traversal detected in archive member: {}".format(member.name))

        if member.issym() or member.islnk():
            link_target = os.path.abspath(os.path.join(base_dir, member.linkname))
            if not is_within_directory(base_dir, link_target):
                raise Exception("Link target escapes extraction dir: {} -> {}".format(member.name, member.linkname))
    tar.extractall(path=base_dir, members=members, numeric_owner=numeric_owner)

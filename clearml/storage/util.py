import fnmatch
import os
import sys
from typing import Optional, Union, Callable, Any

from .filepaths import is_within_directory
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


def is_windows() -> bool:
    """
    :return: True if currently running on windows OS
    """
    return sys.platform == "win32"


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

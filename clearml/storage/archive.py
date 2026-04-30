import os
import tarfile
from typing import Optional, Union, Any
from pathlib import Path

from .filepaths import is_within_directory
from ..debugging.log import LoggerRoot


def create_zip_directories(
    zipfile: Any,
    path: Optional[Union[str, os.PathLike]] = None,
) -> None:
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


def extract_tar_archive(
    archive_path: str,
    suffix: str,  # Literal[".tar.gz", ".tgz"]
    target: str = ".",
    **kwargs: Any,
) -> None:
    """
    Tarfile member sanitization (addresses CVE-2007-4559)
    """
    mode = {
        ".tar.gz": "r",
        ".tgz": "r:gz",
    }[suffix]

    with tarfile.open(archive_path, mode=mode) as tar_file:
        base_dir = os.path.abspath(target)
        for member in tar_file.getmembers():
            flag_path_traversal_vulnerability(
                extraction_folder=base_dir,
                extraction_file_path=member.name,
            )

            if member.issym() or member.islnk():
                flag_symlink_escape_vulnerability(
                    extraction_folder=base_dir,
                    extraction_file_path=member.name,
                    extraction_symlink_target=member.linkname,
                )

        tar_file.extractall(path=base_dir, **kwargs)


def flag_path_traversal_vulnerability(
    extraction_folder: Union[str, Path],
    extraction_file_path: Union[str, Path],
) -> None:
    """
    Raises a ValueError if the extraction_file_path is not contained within the extraction_folder.
    """
    extraction_file_absolute_path = os.path.abspath(os.path.join(
        extraction_folder,
        extraction_file_path,
    ))
    if not is_within_directory(
        extraction_folder,
        extraction_file_absolute_path,
    ):
        raise ValueError(f"Path traversal detected in archive member: {extraction_file_path}")


def flag_symlink_escape_vulnerability(
    extraction_folder: Union[str, Path],
    extraction_file_path: Union[str, Path],
    extraction_symlink_target: Union[str, Path],
) -> None:
    """
    Raises a ValueError if the extraction_symlink_target points outside of the extraction_folder.
    """
    extraction_link_absolute_path = os.path.abspath(os.path.join(
        extraction_folder,
        extraction_symlink_target,
    ))
    if not is_within_directory(
        extraction_folder,
        extraction_link_absolute_path,
    ):
        raise ValueError(f"Link target escapes extraction dir: {extraction_file_path} -> {extraction_symlink_target}")

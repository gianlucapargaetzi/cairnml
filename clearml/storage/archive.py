import os
import tarfile
from typing import Union, Any
from pathlib import Path
from zipfile import ZipFile

from .filepaths import is_within_directory


def extract_zip_archive(
    archive_path: str,
    target: str = ".",
) -> None:
    """
    Extracts a .zip archive file to a target location.

    :param str archive_path: The path to the .zip archive file.
    :param str target: The location in the filesystem where the contents of the .zip archive should be extracted.
    """
    with ZipFile(file=archive_path, mode='r') as zip_file:
        base_directory = os.path.abspath(target)
        for file_path in zip_file.namelist():
            flag_path_traversal_vulnerability(
                extraction_folder=base_directory,
                extraction_file_path=file_path,
            )
            # No need to run flag_symlink_escape_vulnerability
            # zip_file.extractall does not create symlinks

        zip_file.extractall(path=target)


def extract_tar_archive(
    archive_path: str,
    suffix: str,  # Literal[".tar.gz", ".tgz"]
    target: str = ".",
    **kwargs: Any,
) -> None:
    """
    Extracts a .tar.gz or .tgz archive file to a target location after sanitization of the archive's contents.
    Tarfile member sanitization (addresses CVE-2007-4559)

    :param str archive_path: The path to the tar archive file.
    :param str suffix: The full extension of the tar file. Either '.tar.gz' or '.tgz'.
    :param str target: The location in the filesystem where the contents of the tar archive should be extracted.
    """
    mode = {
        ".tar.gz": "r",
        ".tgz": "r:gz",
    }[suffix]

    with tarfile.open(name=archive_path, mode=mode) as tar_file:
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

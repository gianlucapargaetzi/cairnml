"""The top-level ``environment`` and ``files`` sections of the configuration, applied for the agent.

The SDK has functions of the same names (cairnml.backend_config.utils), but not the same behaviour, so the agent keeps
its own:
- apply_files sets the file mode given as ``mode`` (documented in the agent's default configuration); the SDK's does
  not read it;
- apply_environment writes a false value (0, False) as text, and only None as an empty value; the SDK's writes every
  false value as an empty value.
tests/test_backend_config_utils.py pins both. The two helpers that were in this module besides (get_items, get_options)
were the SDK's, unused here, and are gone.
"""

import base64
import os
from os.path import expandvars, expanduser
from pathlib import Path

from pyhocon import HOCONConverter, ConfigTree
from typing import List, TYPE_CHECKING  # noqa: F401  # named by type comments

if TYPE_CHECKING:
    from .config import Config  # noqa: F401  # named by type comments


def apply_environment(config):
    # type: (Config) -> List[str]
    env_vars = config.get("environment", None)
    if not env_vars:
        return []
    if isinstance(env_vars, (list, tuple)):
        env_vars = dict(env_vars)

    keys = list(filter(None, env_vars.keys()))

    for key in keys:
        value = env_vars[key]
        os.environ[str(key)] = str(value if value is not None else "")

    return keys


def apply_files(config):
    # type: (Config) -> None
    files = config.get("files", None)
    if not files:
        return

    if isinstance(files, (list, tuple)):
        files = dict(files)

    print("Creating files from configuration")
    for key, data in files.items():
        path = data.get("path")
        fmt = data.get("format", "string")
        target_fmt = data.get("target_format", "string")
        overwrite = bool(data.get("overwrite", True))
        contents = data.get("contents")
        mode = data.get("mode", None)

        target = Path(expanduser(expandvars(path)))

        # noinspection PyBroadException
        try:
            if target.is_dir():
                print("Skipped [{}]: is a directory {}".format(key, target))
                continue

            if not overwrite and target.is_file():
                print("Skipped [{}]: file exists {}".format(key, target))
                continue
        except Exception as ex:
            print("Skipped [{}]: can't access {} ({})".format(key, target, ex))
            continue

        if contents:
            try:
                if fmt == "base64":
                    contents = base64.b64decode(contents)
                    if target_fmt != "bytes":
                        contents = contents.decode("utf-8")
            except Exception as ex:
                print("Skipped [{}]: failed decoding {} ({})".format(key, fmt, ex))
                continue

        # noinspection PyBroadException
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except Exception as ex:
            print("Skipped [{}]: failed creating path {} ({})".format(key, target.parent, ex))
            continue

        try:
            if target_fmt == "bytes":
                try:
                    target.write_bytes(contents)
                except TypeError:
                    # simpler error so the user won't get confused
                    raise TypeError("a bytes-like object is required") from None
            else:
                try:
                    if target_fmt == "json":
                        text = HOCONConverter.to_json(contents)
                    elif target_fmt in ("yaml", "yml"):
                        text = HOCONConverter.to_yaml(contents)
                    else:
                        if isinstance(contents, ConfigTree):
                            contents = contents.as_plain_ordered_dict()
                        text = str(contents)
                except Exception as ex:
                    print("Skipped [{}]: failed encoding to {} ({})".format(key, target_fmt, ex))
                    continue
                target.write_text(text)
            print("Saved [{}]: {}".format(key, target))
        except Exception as ex:
            print("Skipped [{}]: failed saving file {} ({})".format(key, target, ex))
            continue

        try:
            if mode:
                if isinstance(mode, int):
                    mode = int(str(mode), 8)
                else:
                    mode = int(mode, 8)
                target.chmod(mode)
        except Exception as ex:
            print("Skipped [{}]: failed setting mode {} for {} ({})".format(key, mode, target, ex))
            continue

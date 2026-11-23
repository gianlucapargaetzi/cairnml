"""Parse pip requirement lines: the requirements-parser package plus the behaviour the agent depends on.

Only the package's ``Requirement`` class is used. Its own ``parse`` is not: it has no list input, no optional ``-r``
files and no ``cwd``, and it drops option lines (``--extra-index-url`` among them) instead of leaving them to the
callers.
"""

import os
import re
import warnings

from packaging.requirements import Requirement as PackagingRequirement
from requirements.requirement import URI_REGEX, VCS_REGEX
from requirements.requirement import Requirement as _Requirement

from cairnml_agent.definitions import PIP_EXTRA_INDICES

_EDITABLE = re.compile(r"^(-e|--editable=?)\s*")
_SCP_GIT = "git+git@"  # git+git@host:path, the package's VCS pattern knows git+git:// only
_FILE_SCHEME = "file://"


def _to_url_form(line):
    return line.replace(_SCP_GIT, "git+git://", 1) if line.startswith(_SCP_GIT) else line


class Requirement(_Requirement):
    """A requirement line, parsed the way the agent has always read it."""

    @classmethod
    def parse(cls, line):
        line = line.lstrip()
        if _EDITABLE.match(line):
            return cls.parse_editable(_EDITABLE.sub("", line))
        # as the package does: a hash option and a trailing line continuation are not part of the requirement
        for marker in (" --hash=", " \\"):
            if marker in line:
                line = line[: line.find(marker)]
        if "@" in line and ("#" not in line or line.index("#") > line.index("@")):
            named = cls._parse_named_uri(line)
            if named is not None:
                return named
        return cls.parse_line(line)

    @classmethod
    def parse_line(cls, line):
        scp = line.startswith(_SCP_GIT)
        req = super().parse_line(_to_url_form(line))
        if scp:
            req.line = line
        req.extras = sorted(req.extras)  # the package returns set order, which changes from run to run
        return req

    @classmethod
    def parse_editable(cls, line):
        scp = line.startswith(_SCP_GIT)
        req = super().parse_editable(_to_url_form(line))
        if scp:
            req.line = "-e {}".format(line)
        elif req.local_file and "#" not in line:
            # a path without #egg is kept whole, the package would cut it at the last slash
            req.path = line[len(_FILE_SCHEME) :] if line.startswith(_FILE_SCHEME) else line
            req.name = None
        return req

    @classmethod
    def _parse_named_uri(cls, line):
        """``name @ url``: returns None when the line is not of that form."""
        name, uri = (part.strip() for part in line.split("@", 1))
        try:
            PackagingRequirement(name)
            if VCS_REGEX.match(uri) or uri.startswith(_SCP_GIT):
                req = cls.parse_line(uri)
                req.name = name
                return req
            if URI_REGEX.match(uri):
                req = cls.parse_line(uri)
                req.name = name
                req.line = line
                return req
        except Exception:
            pass
        return None


def parse(reqstr, cwd=None):
    """
    Parse a requirements file into a generator of Requirements.

    :param reqstr: a string, a list of lines or a file like object containing requirements
    :param cwd: optional folder that ``-r file.txt`` lines are resolved against
    """
    if not reqstr:
        return

    filename = None
    if isinstance(reqstr, list) and isinstance(reqstr[0], str):
        lines = reqstr
    else:
        filename = getattr(reqstr, "name", None)
        lines = (reqstr if isinstance(reqstr, str) else reqstr.read()).splitlines()

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--requirement ")):
            _, new_filename = line.split()
            base = os.path.dirname(filename or ".") if filename or not cwd else cwd
            path = os.path.join(base, new_filename)
            if os.path.exists(path):
                with open(path) as f:
                    yield from parse(f)
        elif line.startswith(("-f", "--find-links", "-i", "--index-url", "--no-index")):
            warnings.warn("Private repos not supported. Skipping.")
        elif line.startswith("--extra-index-url"):
            extra_index = re.sub(r"\s+#.*$", "", line[len("--extra-index-url") :].strip())
            if extra_index and extra_index not in PIP_EXTRA_INDICES:
                PIP_EXTRA_INDICES.append(extra_index)
                print(f"appended {extra_index} to list of extra pip indices")
        elif line.startswith(("-Z", "--always-unzip")):
            warnings.warn("Unused option --always-unzip. Skipping.")
        else:
            yield Requirement.parse(line)

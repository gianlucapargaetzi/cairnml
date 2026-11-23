"""The packages a task's code needs, for the requirements the SDK records with a task.

The imports are read by pigar 2.x in a child process (``project_requirements_worker``), so that pigar's own
side effects on ``sys.path`` and ``sys.modules`` never reach the training process. This module decides, from those
imports, which names are packages to install (the installed ones), which are not installed (guesses) and which are
local modules of the environment (listed, not installed).
"""

import collections
import fnmatch
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Set, Tuple, Union

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "project_requirements_worker.py")
SCAN_TIMEOUT_SECONDS = 120

_SPECIAL_CASES = {
    "dogpile.cache": "dogpile.cache",
    "dogpile.core": "dogpile.core",
    "ruamel.yaml": "ruamel.yaml",
    "ruamel.ordereddict": "ruamel.ordereddict",
}


class _Locations(dict):
    """Code locations of a name: the file and its line numbers."""

    def __init__(self) -> None:
        super(_Locations, self).__init__()
        self._sorted = None

    def add(self, file: str, lineno: int) -> None:
        if file in self and lineno not in self[file]:
            self[file].append(lineno)
        else:
            self[file] = [lineno]

    def extend(self, obj: "_Locations") -> None:
        for file, linenos in obj.items():
            for lineno in linenos:
                self.add(file, lineno)

    def sorted_items(self) -> List[str]:
        if self._sorted is None:
            self._sorted = [
                "{0}: {1}".format(f, ",".join([str(n) for n in sorted(ls)])) for f, ls in sorted(self.items())
            ]
        return self._sorted


class ImportedModules(dict):
    """The names a task imports, each with its locations."""

    def add(self, name: str, file: str, lineno: int) -> None:
        if name is None:
            return
        for counted in counted_names(name):
            if counted not in self:
                self[counted] = _Locations()
            self[counted].add(file, lineno)

    def __or__(self, obj: "ImportedModules") -> "ImportedModules":
        for name, locations in obj.items():
            for file, linenos in locations.items():
                for lineno in linenos:
                    self.add(name, file, lineno)
        return self


class ReqsModules(dict):
    """The requirements to record: each package with its version and the locations that need it."""

    _Detail = collections.namedtuple("Detail", ["version", "comments"])

    def __init__(self) -> None:
        super(ReqsModules, self).__init__()
        self._sorted = None

    def add(self, package: str, version: str, locations: Any) -> None:
        if package in self:
            self[package].comments.extend(locations)
        else:
            self[package] = self._Detail(version, locations)
        self._sorted = None

    def sorted_items(self) -> List[Tuple[Any, Any]]:
        if self._sorted is None:
            self._sorted = sorted(self.items())
        return self._sorted

    def remove(self, *names: Any) -> None:
        for name in names:
            if name in self:
                self.pop(name)
        self._sorted = None


def counted_names(name: str) -> List[str]:
    """The names an import is counted as: its top-level package, with the exceptions the SDK has always made."""
    if not name or name.startswith("."):
        return []
    if name.startswith("flask.ext.") and len(name.split(".")) > 2:
        return ["flask", "flask_" + name.split(".")[2]]
    special_name = ".".join(name.split(".")[:2])
    if special_name in _SPECIAL_CASES:
        return [_SPECIAL_CASES[special_name]]
    if "." in name:
        return [name.split(".")[0]]
    return [name]


def _run_worker(payload: Dict[str, Any]) -> Dict[str, Any]:
    # -I: the child does not read the environment's PYTHONPATH, so it cannot pick up a module of the project by accident
    try:
        proc = subprocess.run(
            [sys.executable, "-I", WORKER],
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=SCAN_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        raise RuntimeError("the requirement scan did not finish in {} seconds".format(SCAN_TIMEOUT_SECONDS)) from err
    if proc.returncode != 0:
        # the exception is the last line of the worker's output; the lines before it are the context
        detail = proc.stderr.decode("utf-8", "replace")[-4000:].strip()
        lines = detail.splitlines()
        cause = lines[-1] if lines else "the worker printed nothing"
        if len(lines) > 1:
            cause += " (worker output: {})".format(" | ".join(lines))
        raise RuntimeError("the requirement scan failed: {}".format(cause))
    return json.loads(proc.stdout.decode("utf-8"))


def _imports_into(data: Dict[str, Any], modules: ImportedModules) -> Tuple[Set[str], Set[str]]:
    """Adds the scanned imports to ``modules``; returns the relative imports and the names imported in a try."""
    relative: Set[str] = set()
    try_imports: Set[str] = set()
    for name, is_try, file, lineno in data["imports"]:
        if name and name.startswith("."):
            relative.add(name)
            continue
        modules.add(name, file, lineno)
        if is_try:
            try_imports.update(counted_names(name))
    return relative, try_imports


def _is_inside(path: str, folder: str) -> bool:
    folder = os.path.realpath(folder)
    path = os.path.realpath(path)
    return path == folder or path.startswith(folder + os.sep)


class GenerateReqs(object):
    """Finds the requirements of a project, or of one entry script of it, against the installed packages."""

    _force_modules_reqs: Dict[str, str] = dict()

    def __init__(self, project_path: str, ignores: list, installed_pkgs: dict) -> None:
        self._project_path = project_path
        self._ignores = ignores
        self._installed_pkgs = installed_pkgs
        # the names the last scan found in the code, before the module callback (None before a scan)
        self.found_names: Optional[Set[str]] = None

    def extract_reqs(
        self,
        module_callback: Any = None,
        entry_point_filename: Optional[str] = None,
    ) -> Tuple[ReqsModules, Set[str], ReqsModules, ReqsModules]:
        """Requirements (installed), try imports, guesses (not installed) and local modules of the environment."""
        if self.__module__:
            our_module = self.__module__.split(".")[0]
            if our_module and our_module not in self._force_modules_reqs:
                from ..version import __version__

                self._force_modules_reqs[our_module] = __version__

        modules = ImportedModules()
        data = self._scan(entry_point_filename, modules)
        relative, try_imports = _imports_into(data, modules)
        self.found_names = set(modules.keys())
        origins = data.get("origins", {})

        if module_callback:
            modules = module_callback(modules)

        reqs = ReqsModules()
        guess = ReqsModules()
        local = ReqsModules()
        candidates: Set[str] = set()
        for name in list(modules.keys()):
            origin = origins.get(name, "installed")
            if origin == "std":
                continue
            if isinstance(origin, dict):
                path = origin.get("local")
                # a module of the project itself is part of the task, not a package to record
                if path and _is_inside(path, self._project_path):
                    continue
                if path and not is_base_module(path):
                    relpath = os.path.relpath(path, self._project_path)
                    if not relpath.startswith("."):
                        relpath = "." + os.path.sep + relpath
                    local.add(name, relpath, modules[name])
                continue
            candidates.add(name)
        candidates |= set(self._force_modules_reqs.keys())

        for name in sorted(candidates):
            if name in self._installed_pkgs:
                pkginfo = self._installed_pkgs[name]
                for pkg, (pkg_name, version) in ({"": pkginfo} if not isinstance(pkginfo, dict) else pkginfo).items():
                    _name = pkg or name
                    reqs.add(*_requirement(version, pkg_name, _name, modules))
            elif name in modules:
                guess.add(name, 0, modules[name])

        return reqs, try_imports, guess, local

    def _scan(self, entry_point_filename: Optional[str], modules: ImportedModules) -> Dict[str, Any]:
        """The entry script's imports when it is self-contained, else the whole project's."""
        entry = self._entry_path(entry_point_filename)
        if entry:
            data = _run_worker({"project_root": self._project_path, "ignores": self._ignores, "entry": entry})
            # only a module of the project itself makes the scan whole-project; a package installed elsewhere (an
            # editable install) is local too, but it does not
            uses_project = any(
                isinstance(origin, dict)
                and bool(origin.get("local"))
                and _is_inside(origin["local"], self._project_path)
                for origin in data.get("origins", {}).values()
            )
            relative = any(name and name.startswith(".") for name, *_ in data["imports"])
            # the whole-project scan needs a folder; for a step file or a single script the entry's result stands
            if not (uses_project or relative) or not os.path.isdir(self._project_path):
                return data
        return _run_worker({"project_root": self._project_path, "ignores": self._ignores})

    def _entry_path(self, entry_point_filename: Optional[str]) -> Optional[str]:
        if not entry_point_filename:
            return None
        if not os.path.isabs(entry_point_filename):
            if not os.path.isdir(self._project_path):
                return None
            entry_point_filename = os.path.join(self._project_path, entry_point_filename)
        return entry_point_filename if os.path.isfile(entry_point_filename) else None

    @classmethod
    def get_forced_modules(cls) -> dict:
        return cls._force_modules_reqs

    @classmethod
    def add_forced_module(cls, module_name: str, module_version: str) -> None:
        cls._force_modules_reqs[module_name] = module_version


def file_import_modules(fname: str, code: Union[str, bytes]) -> Tuple[ImportedModules, Set[str]]:
    """The imports of one script or notebook cell, with the names imported in a try."""
    modules = ImportedModules()
    text = code.decode("utf-8", "replace") if isinstance(code, bytes) else code
    data = _run_worker({"project_root": "", "file": fname, "code": text})
    _, try_imports = _imports_into(data, modules)
    return modules, try_imports


def _requirement(version: str, pkg_name: str, name: str, modules: ImportedModules) -> Tuple[str, str, Any]:
    if name not in modules:
        modules.add(name, name, 0)
    if not version and pkg_name and pkg_name.startswith("-e "):
        return ("{} @ {}".format(name, pkg_name.replace("-e ", "", 1)), version, modules[name])
    return (pkg_name, version, modules[name])


# --- installed packages: which import name is which distribution, read from the metadata in sys.path ---------------
def get_installed_pkgs_detail() -> Dict[str, Union[Tuple[str, str], Dict[str, Tuple[str, str]]]]:
    """
    HACK: bugfix of the original pigar get_installed_pkgs_detail

    Get mapping for import top level name
    and install package name with version.
    """
    mapping = dict()

    for path in sys.path:
        if os.path.isdir(path) and path.rstrip("/").endswith(("site-packages", "dist-packages")):
            new_mapping = _search_path(path)
            # BUGFIX:
            # override with previous, just like python resolves imports, the first match is the one used.
            # unlike the original implementation, where the last one is used.
            new_mapping.update(mapping)
            mapping = new_mapping

    # HACK: prefer tensorflow_gpu over tensorflow
    if "tensorflow_gpu" in mapping:
        mapping["tensorflow"] = mapping["tensorflow_gpu"]

    # HACK: prefer tensorflow_macos over tensorflow
    if "tensorflow_macos" in mapping:
        p = mapping.pop("tensorflow_macos", None)
        if p and isinstance(p, (tuple, list)) and len(p) == 2:
            mapping["tensorflow"] = ("tensorflow", p[1])

    return mapping


def is_base_module(module_path: str) -> bool:
    python_base = "{}python{}.{}".format(os.sep, sys.version_info.major, sys.version_info.minor)
    for path in sys.path:
        if os.path.isdir(path) and path.rstrip("/").endswith((python_base,)):
            if not path[-1] == os.sep:
                path += os.sep
            if module_path.startswith(path):
                return True
    return False


def _search_path(path: str) -> dict:
    mapping = dict()

    for file in os.listdir(path):
        # Install from PYPI.

        # broken pip packages might start with '~' - ignore it
        if str(file).startswith("~"):
            continue

        if fnmatch.fnmatch(file, "*-info"):
            pkg_name, version = file.split("-")[:2]
            if version.endswith("dist"):
                version = version.rsplit(".", 1)[0]
            # Issue for ubuntu: sudo pip install xxx
            elif version.endswith("egg"):
                version = version.rsplit(".", 1)[0]

            mapping_pkg_name = pkg_name
            # pep610 support. add support for new pip>=20.1 git reference feature
            git_direct_json = os.path.join(path, file, "direct_url.json")
            if os.path.isfile(git_direct_json):
                # noinspection PyBroadException
                try:
                    with open(git_direct_json, "r") as f:
                        direct_json = json.load(f)

                    if "vcs_info" in direct_json:
                        vcs_info = direct_json["vcs_info"]
                        git_url = "{vcs}+{url}@{commit}#egg={package}".format(
                            vcs=vcs_info["vcs"],
                            url=direct_json["url"],
                            commit=vcs_info["commit_id"],
                            package=pkg_name,
                        )
                        # If subdirectory is present, append this to the git_url
                        if "subdirectory" in direct_json:
                            git_url = "{git_url}&subdirectory={subdirectory}".format(
                                git_url=git_url,
                                subdirectory=direct_json["subdirectory"],
                            )
                        # Bugfix: package name should be the URL link, because we need it unique
                        # mapping[pkg_name] = ('-e', git_url)
                        pkg_name, version = "-e {}".format(git_url), ""
                    elif "url" in direct_json:
                        url_link = direct_json.get("url", "").strip().lower()
                        if url_link and not url_link.startswith("file://"):
                            git_url = direct_json["url"]
                            # If subdirectory is present, append this to the git_url
                            if "subdirectory" in direct_json:
                                git_url = "{git_url}#subdirectory={subdirectory}".format(
                                    git_url=direct_json["url"],
                                    subdirectory=direct_json["subdirectory"],
                                )

                            pkg_name, version = git_url, ""

                except Exception:
                    pass

            # default
            mapping[mapping_pkg_name] = (pkg_name, version)

            # analyze 'top_level.txt' if it exists
            top_level = os.path.join(path, file, "top_level.txt")
            if not os.path.isfile(top_level):
                continue
            with open(top_level, "r") as f:
                for line in f:
                    top_package = line.strip()
                    # NOTICE: this is a namespace package
                    if top_package and mapping_pkg_name.startswith("{}_".format(top_package)):
                        top = mapping.get(top_package, dict())
                        if not isinstance(top, dict):
                            top = {top_package: top}
                        top[mapping_pkg_name] = (pkg_name, version)
                        mapping[top_package] = top
                    else:
                        mapping[top_package] = (pkg_name, version)

        # Install from local and available in GitHub.
        elif fnmatch.fnmatch(file, "*-link"):
            link = os.path.join(path, file)
            if not os.path.isfile(link):
                continue
            # Link path.
            with open(link, "r") as f:
                for line in f:
                    line = line.strip()
                    if line != ".":
                        dev_dir = line
            if not dev_dir:
                continue
            if not os.path.exists(dev_dir):
                continue
            # Egg info path.
            info_dir = [_file for _file in os.listdir(dev_dir) if _file.endswith("egg-info")]
            if not info_dir:
                continue
            info_dir = info_dir[0]
            top_level = os.path.join(dev_dir, info_dir, "top_level.txt")
            # Check whether it can be imported.
            if not os.path.isfile(top_level):
                continue

            # Check .git dir.
            git_path = os.path.join(dev_dir, ".git")
            if os.path.isdir(git_path):
                config = parse_git_config(git_path)
                url = config.get('remote "origin"', {}).get("url")
                if not url:
                    continue
                branch = 'branch "master"'
                if branch not in config:
                    for section in config:
                        if "branch" in section:
                            branch = section
                            break
                if not branch:
                    continue
                branch = branch.split()[1][1:-1]

                pkg_name = info_dir.split(".egg")[0]
                git_url = "git+{0}@{1}#egg={2}".format(url, branch, pkg_name)
                with open(top_level, "r") as f:
                    for line in f:
                        # Bugfix: package name should be the URL link, because we need it unique
                        # mapping[line.strip()] = ('-e', git_url)
                        mapping[line.strip()] = ("-e {}".format(git_url), "")

    return mapping


def parse_git_config(path: str) -> dict:
    """Parse git config file."""
    config = dict()
    section = None

    with open(os.path.join(path, "config"), "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("["):
                section = line[1:-1].strip()
                config[section] = dict()
            elif section:
                key, value = line.replace(" ", "").split("=")
                config[section][key] = value
    return config

"""Pin what the package managers and the resolver do with pip requirement lines.

The tests reach the parser only through cairnml_agent.helper.package.requirements, so they stay the same
when the parser behind it changes.
"""

import warnings
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest

from cairnml_agent import definitions
from cairnml_agent.helper.package.requirements import MarkerRequirement, Requirement, RequirementsManager, parse

DEFAULTS = dict(
    name=None,
    extras=[],
    specs=[],
    specifier=False,
    uri=None,
    vcs=None,
    revision=None,
    local_file=False,
    editable=False,
    path=None,
    subdirectory=None,
    hash_name=None,
    hash=None,
)

# (input line, expected .line, attributes that differ from DEFAULTS, expected MarkerRequirement.tostr())
LINES = [
    ("numpy", "numpy", {"name": "numpy", "specifier": True}, "numpy"),
    ("numpy==1.2", "numpy==1.2", {"name": "numpy", "specs": [("==", "1.2")], "specifier": True}, "numpy==1.2"),
    ("Numpy>=1.0", "Numpy>=1.0", {"name": "Numpy", "specs": [(">=", "1.0")], "specifier": True}, "Numpy>=1.0"),
    ("A_B==2", "A_B==2", {"name": "A_B", "specs": [("==", "2")], "specifier": True}, "A_B==2"),
    (
        "numpy>=1.0,<2.0",
        "numpy>=1.0,<2.0",
        {"name": "numpy", "specs": [("<", "2.0"), (">=", "1.0")], "specifier": True},
        "numpy>=1.0,<2.0",
    ),
    ("numpy~=1.4.2", "numpy~=1.4.2", {"name": "numpy", "specs": [("~=", "1.4.2")], "specifier": True}, "numpy~=1.4.2"),
    ("numpy!=1.3", "numpy!=1.3", {"name": "numpy", "specs": [("!=", "1.3")], "specifier": True}, "numpy!=1.3"),
    ("torch >=1.0", "torch >=1.0", {"name": "torch", "specs": [(">=", "1.0")], "specifier": True}, "torch>=1.0"),
    (
        "requests[security]>=2.0",
        "requests[security]>=2.0",
        {"name": "requests", "extras": ["security"], "specs": [(">=", "2.0")], "specifier": True},
        "requests[security]>=2.0",
    ),
    (
        "x[Alpha,beta,gamma,delta]>=1",
        "x[Alpha,beta,gamma,delta]>=1",
        {"name": "x", "extras": ["alpha", "beta", "delta", "gamma"], "specs": [(">=", "1")], "specifier": True},
        "x[alpha,beta,delta,gamma]>=1",
    ),
    (
        "pkg ; python_version < '3.8'",
        "pkg ; python_version < '3.8'",
        {"name": "pkg", "specifier": True},
        "pkg; python_version < '3.8'",
    ),
    (
        "pkg==1.0 ; sys_platform == 'win32'",
        "pkg==1.0 ; sys_platform == 'win32'",
        {"name": "pkg", "specs": [("==", "1.0")], "specifier": True},
        "pkg==1.0; sys_platform == 'win32'",
    ),
    (
        "name @ https://host/x/torch-2.0.0%2Bcu118-cp310-cp310-linux_x86_64.whl",
        "name @ https://host/x/torch-2.0.0%2Bcu118-cp310-cp310-linux_x86_64.whl",
        {"name": "name", "uri": "https://host/x/torch-2.0.0%2Bcu118-cp310-cp310-linux_x86_64.whl"},
        "https://host/x/torch-2.0.0%2Bcu118-cp310-cp310-linux_x86_64.whl",
    ),
    (
        "name @ file:///x/pkg-1.0-py3-none-any.whl",
        "name @ file:///x/pkg-1.0-py3-none-any.whl",
        {"name": "name", "uri": "file:///x/pkg-1.0-py3-none-any.whl", "local_file": True},
        "name @ file:///x/pkg-1.0-py3-none-any.whl",
    ),
    (
        "name @ https://host/x.whl#sha256=abc123",
        "name @ https://host/x.whl#sha256=abc123",
        {"name": "name", "uri": "https://host/x.whl", "hash_name": "sha256", "hash": "abc123"},
        "https://host/x.whl",
    ),
    (
        "name @ git+https://github.com/a/b.git@v1",
        "git+https://github.com/a/b.git@v1",
        {"name": "name", "uri": "git+https://github.com/a/b.git", "vcs": "git", "revision": "v1"},
        "git+https://github.com/a/b.git@v1",
    ),
    (
        "b[x] @ git+https://github.com/a/b.git",
        "git+https://github.com/a/b.git",
        {"name": "b[x]", "uri": "git+https://github.com/a/b.git", "vcs": "git"},
        "git+https://github.com/a/b.git",
    ),
    (
        "git+https://github.com/a/b.git#egg=b",
        "git+https://github.com/a/b.git#egg=b",
        {"name": "b", "uri": "git+https://github.com/a/b.git", "vcs": "git"},
        "git+https://github.com/a/b.git#egg=b",
    ),
    (
        "git+https://github.com/a/b.git@main#egg=b&subdirectory=sub",
        "git+https://github.com/a/b.git@main#egg=b&subdirectory=sub",
        {"name": "b", "uri": "git+https://github.com/a/b.git", "vcs": "git", "revision": "main", "subdirectory": "sub"},
        "git+https://github.com/a/b.git@main#egg=b&subdirectory=sub",
    ),
    (
        "git+ssh://git@github.com/a/b.git@v2#egg=b[extra]",
        "git+ssh://git@github.com/a/b.git@v2#egg=b[extra]",
        {"name": "b", "extras": ["extra"], "uri": "git+ssh://git@github.com/a/b.git", "vcs": "git", "revision": "v2"},
        "git+ssh://git@github.com/a/b.git@v2#egg=b[extra]",
    ),
    (
        "git+git@github.com:a/b.git#egg=b",
        "git+git@github.com:a/b.git#egg=b",
        {"name": "b", "uri": "git+git://github.com:a/b.git", "vcs": "git"},
        "git+git@github.com:a/b.git#egg=b",
    ),
    (
        "git+git@github.com:a/b.git@dev#egg=b",
        "git+git@github.com:a/b.git@dev#egg=b",
        {"name": "b", "uri": "git+git://github.com:a/b.git", "vcs": "git", "revision": "dev"},
        "git+git@github.com:a/b.git@dev#egg=b",
    ),
    (
        "-e git+git@github.com:a/b.git#egg=b",
        "-e git+git@github.com:a/b.git#egg=b",
        {"name": "b", "uri": "git+git://github.com:a/b.git", "vcs": "git", "editable": True},
        "-e git+git@github.com:a/b.git#egg=b",
    ),
    (
        "-e git+https://github.com/a/b.git@v1#egg=b",
        "-e git+https://github.com/a/b.git@v1#egg=b",
        {"name": "b", "uri": "git+https://github.com/a/b.git", "vcs": "git", "revision": "v1", "editable": True},
        "-e git+https://github.com/a/b.git@v1#egg=b",
    ),
    (
        "--editable=git+https://github.com/a/b.git#egg=b",
        "-e git+https://github.com/a/b.git#egg=b",
        {"name": "b", "uri": "git+https://github.com/a/b.git", "vcs": "git", "editable": True},
        "-e git+https://github.com/a/b.git#egg=b",
    ),
    ("-e /abs/path", "-e /abs/path", {"local_file": True, "editable": True, "path": "/abs/path"}, "-e /abs/path"),
    ("-e ./pkg", "-e ./pkg", {"local_file": True, "editable": True, "path": "./pkg"}, "-e ./pkg"),
    (
        "-e /abs/path#egg=pk",
        "-e /abs/path#egg=pk",
        {"name": "pk", "local_file": True, "editable": True, "path": "/abs/path"},
        "pk",
    ),
    (
        "-e file:///abs/path#egg=pk",
        "-e file:///abs/path#egg=pk",
        {"name": "pk", "local_file": True, "editable": True, "path": "/abs/path"},
        "pk",
    ),
    (
        "-e file:///abs/path",
        "-e file:///abs/path",
        {"local_file": True, "editable": True, "path": "/abs/path"},
        "-e file:///abs/path",
    ),
    (
        "https://host/p/pkg-1.0.tar.gz",
        "https://host/p/pkg-1.0.tar.gz",
        {"uri": "https://host/p/pkg-1.0.tar.gz"},
        "https://host/p/pkg-1.0.tar.gz",
    ),
    (
        "https://host/p/pkg-1.0.tar.gz#egg=pkg&sha256=ab",
        "https://host/p/pkg-1.0.tar.gz#egg=pkg&sha256=ab",
        {"name": "pkg", "uri": "https://host/p/pkg-1.0.tar.gz", "hash_name": "sha256", "hash": "ab"},
        "https://host/p/pkg-1.0.tar.gz",
    ),
    (
        "file:///x/pkg-1.0.tar.gz",
        "file:///x/pkg-1.0.tar.gz",
        {"uri": "file:///x/pkg-1.0.tar.gz", "local_file": True},
        "file:///x/pkg-1.0.tar.gz",
    ),
    ("/abs/pkg#egg=pkg", "/abs/pkg#egg=pkg", {"name": "pkg", "local_file": True, "path": "/abs/pkg"}, "pkg"),
    ("./local#egg=loc", "./local#egg=loc", {"name": "loc", "local_file": True, "path": "./local"}, "loc"),
    ("  numpy==1.0", "numpy==1.0", {"name": "numpy", "specs": [("==", "1.0")], "specifier": True}, "numpy==1.0"),
    (
        "svn+https://host/repo#egg=r",
        "svn+https://host/repo#egg=r",
        {"name": "r", "uri": "svn+https://host/repo", "vcs": "svn"},
        "svn+https://host/repo#egg=r",
    ),
    (
        "hg+https://host/repo@rev#egg=r",
        "hg+https://host/repo@rev#egg=r",
        {"name": "r", "uri": "hg+https://host/repo", "vcs": "hg", "revision": "rev"},
        "hg+https://host/repo@rev#egg=r",
    ),
]

# lines the parser refuses; the callers rely on that to pass pip options through untouched
RAISING_LINES = [
    "# comment",
    "--no-binary :all:",
    "-c cons.txt",
    "--pre",
    "numpy==",
    "",
    "-e",
]


@pytest.fixture
def extra_indices():
    saved = list(definitions.PIP_EXTRA_INDICES)
    del definitions.PIP_EXTRA_INDICES[:]
    yield definitions.PIP_EXTRA_INDICES
    definitions.PIP_EXTRA_INDICES[:] = saved


@pytest.mark.parametrize("line,expected_line,expected,expected_str", LINES, ids=[row[0] for row in LINES])
def test_a_requirement_line_is_parsed_into_these_attributes(line, expected_line, expected, expected_str):
    req = Requirement.parse(line)
    actual = {
        name: sorted(getattr(req, name)) if name in ("specs", "extras") else getattr(req, name) for name in DEFAULTS
    }
    assert req.line == expected_line
    assert actual == dict(DEFAULTS, **expected)
    assert MarkerRequirement(req).tostr() == expected_str


@pytest.mark.parametrize("line", RAISING_LINES)
def test_a_line_that_is_not_a_requirement_raises(line):
    with pytest.raises(Exception):
        Requirement.parse(line)


# a trailing comment or hash option is refused by today's parser
@pytest.mark.parametrize("line", ["a == 1 # c", "numpy==1.2 --hash=sha256:abc", "numpy==1.2 \\"])
def test_a_requirement_followed_by_a_comment_or_hash_option_raises(line):
    with pytest.raises(Exception):
        Requirement.parse(line)


def test_a_marker_requirement_is_hashable_and_copies_by_value():
    first = MarkerRequirement(Requirement.parse("numpy==1.2"))
    second = first.clone()
    second.req.name = "other"
    assert first.name == "numpy"
    assert first == MarkerRequirement(Requirement.parse("numpy==1.2"))
    assert hash(first) == hash(MarkerRequirement(Requirement.parse("numpy==1.2")))
    assert len({first, MarkerRequirement(Requirement.parse("numpy==1.2"))}) == 1


def test_parsing_a_string_yields_one_requirement_per_line_and_skips_comments_and_blanks():
    reqs = list(parse("numpy==1.0\n\n# a comment\n  torch>=2\n"))
    assert [(r.name, r.specs) for r in reqs] == [("numpy", [("==", "1.0")]), ("torch", [(">=", "2")])]


def test_parsing_nothing_yields_nothing():
    assert list(parse("")) == []
    assert list(parse(None)) == []
    assert list(parse([])) == []


def test_parsing_a_list_of_lines_yields_one_requirement_per_line():
    assert [r.name for r in parse(["numpy", "torch==1"])] == ["numpy", "torch"]


def test_a_file_object_is_read_and_its_r_lines_resolve_next_to_it(tmp_path):
    (tmp_path / "inc.txt").write_text("scipy==1.1\n")
    main = tmp_path / "main.txt"
    main.write_text("numpy\n-r inc.txt\n--requirement inc.txt\n")
    with open(main) as f:
        assert [r.name for r in parse(f)] == ["numpy", "scipy", "scipy"]


def test_an_r_line_in_a_string_resolves_against_cwd(tmp_path):
    (tmp_path / "inc.txt").write_text("scipy==1.1\n")
    assert [r.name for r in parse("numpy\n-r inc.txt", cwd=str(tmp_path))] == ["numpy", "scipy"]


def test_an_r_line_to_a_missing_file_is_skipped(tmp_path):
    assert [r.name for r in parse("numpy\n-r nothing.txt\ntorch", cwd=str(tmp_path))] == ["numpy", "torch"]


def test_an_extra_index_url_is_collected_once_printed_and_not_yielded(extra_indices, capsys):
    text = "--extra-index-url https://a.example/simple # why\n--extra-index-url https://a.example/simple\nnumpy"
    assert [r.name for r in parse(text)] == ["numpy"]
    assert extra_indices == ["https://a.example/simple"]
    assert capsys.readouterr().out.count("appended https://a.example/simple to list of extra pip indices") == 1


@pytest.mark.parametrize(
    "line,message",
    [
        ("-i https://x", "Private repos not supported"),
        ("--index-url https://x", "Private repos not supported"),
        ("-f https://x", "Private repos not supported"),
        ("--find-links https://x", "Private repos not supported"),
        ("--no-index", "Private repos not supported"),
        ("-Z", "Unused option --always-unzip"),
        ("--always-unzip", "Unused option --always-unzip"),
    ],
)
def test_an_unsupported_option_warns_and_is_not_yielded(line, message):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert [r.name for r in parse(line + "\nnumpy")] == ["numpy"]
    assert [message in str(w.message) for w in caught] == [True]


def test_an_unknown_option_line_raises_when_it_is_reached():
    with pytest.raises(Exception):
        list(parse("numpy\n--no-binary :all:"))


def test_a_list_with_an_r_line_fails_today():
    # known defect of the copy: the list branch never sets the file name (fixed with the replacement)
    with pytest.raises(UnboundLocalError):
        list(parse(["numpy", "-r inc.txt"]))


def test_the_manager_parses_a_string_a_list_and_keeps_unparsable_lines_verbatim():
    parse_section = RequirementsManager.parse_requirements_section_to_marker_requirements
    assert [str(r) for r in parse_section("numpy==1.0\ntorch>=2")] == ["numpy==1.0", "torch>=2"]
    assert [str(r) for r in parse_section(["numpy==1.0", "torch>=2"])] == ["numpy==1.0", "torch>=2"]
    assert [str(r) for r in parse_section("numpy\n--no-binary :all:\n-c cons.txt")] == [
        "numpy",
        "--no-binary :all:",
        "-c cons.txt",
    ]
    assert parse_section("") == ()


def test_the_manager_resolves_r_lines_in_a_string_against_cwd(tmp_path):
    (tmp_path / "inc.txt").write_text("scipy==1.1\n")
    reqs = RequirementsManager.parse_requirements_section_to_marker_requirements("numpy\n-r inc.txt", cwd=str(tmp_path))
    assert [str(r) for r in reqs] == ["numpy", "scipy==1.1"]


def test_the_manager_parses_a_list_line_by_line_so_an_r_line_to_a_missing_file_is_dropped():
    reqs = RequirementsManager.parse_requirements_section_to_marker_requirements(["numpy", "-r inc.txt"])
    assert [str(r) for r in reqs] == ["numpy"]


def test_get_specific_package_version_returns_the_first_spec_version():
    from cairnml_agent.helper.package.base import get_specific_package_version

    assert get_specific_package_version({"pip": "numpy\ntorch==2.0.0"}, "torch") == "2.0.0"
    assert get_specific_package_version({"pip": ["numpy", "torch==2.0.0"]}, "torch") == "2.0.0"
    assert get_specific_package_version({"pip": "numpy", "org_pip": "torch>=1.5"}, "torch") == "1.5"
    assert get_specific_package_version({"pip": "numpy"}, "torch") == []
    assert get_specific_package_version({}, "torch") == []


def _uv_with_freeze_output(output):
    from cairnml_agent.helper.package.uv_api import UvAPI

    uv = object.__new__(UvAPI)
    uv._installed = True
    uv.lockfile_path = Path("/proj")
    uv.lock_config = mock.MagicMock(enabled=True)
    uv.lock_config.run.return_value = output
    return uv


def test_uv_freeze_makes_an_editable_install_relative_to_the_lock_folder():
    uv = _uv_with_freeze_output("-e /proj/pkg\nnumpy==1.0\n-e git+https://github.com/a/b.git@v1#egg=b")
    assert uv.freeze() == {"pip": ["-e pkg", "numpy==1.0", "-e git+https://github.com/a/b.git@v1#egg=b"]}


def test_uv_freeze_keeps_a_file_url_requirement():
    uv = _uv_with_freeze_output("name @ file:///x/pkg-1.0-py3-none-any.whl")
    assert uv.freeze() == {"pip": ["name @ file:///x/pkg-1.0-py3-none-any.whl"]}


def _conda():
    from cairnml_agent.helper.package.conda_api import CondaAPI

    conda = object.__new__(CondaAPI)
    conda.session = mock.MagicMock()
    conda.session.debug_mode = False
    conda.python = "3.9"
    conda.requirements_manager = mock.MagicMock()
    return conda


def test_conda_keeps_a_marker_that_matches_the_python_version_and_drops_one_that_does_not():
    conda = _conda()
    kept = conda._parse_package_marker_match_python_ver(line="numpy==1.0 ; python_version >= '3.0'")
    assert kept.name == "numpy"
    dropped = conda._parse_package_marker_match_python_ver(line="numpy==1.0 ; python_version < '2.0'")
    assert not dropped


def test_conda_replaces_vcs_requirements_in_the_pip_section_with_the_original_line():
    conda = _conda()
    conda.extra_channels = []
    conda.path = "/env"
    conda.pip = mock.MagicMock()
    conda.session.config.get.return_value = 0
    conda._run_command = mock.MagicMock(return_value={})
    conda._parse_conda_result_bad_packges = mock.MagicMock(return_value=[])

    @contextmanager
    def temp_file(*args, **kwargs):
        yield "env.yml"

    conda.temp_file = temp_file
    env = {"dependencies": ["numpy=1.0", {"pip": ["b==1.0", "other==2.0"]}]}
    requirements = {"pip": ["b @ git+https://github.com/a/b.git@v1", "other==2.0"]}
    conda._load_conda_full_env(env, requirements)
    installed = conda.pip.load_requirements.call_args[0][0]["pip"].splitlines()
    assert installed == ["git+https://github.com/a/b.git@v1", "other==2.0"]


def _pytorch_substitution(original, replacement):
    from cairnml_agent.helper.package.pytorch import PytorchRequirement

    sub = object.__new__(PytorchRequirement)
    sub._original_req = [
        (MarkerRequirement(Requirement.parse(original)), MarkerRequirement(Requirement.parse(replacement)))
    ]
    return sub


WHEEL = "https://download.pytorch.org/whl/cu118/torch-2.0.0%2Bcu118-cp310-cp310-linux_x86_64.whl"


def test_pytorch_replace_back_for_a_wheel_url_line():
    sub = _pytorch_substitution("torch", WHEEL)
    result = sub.replace_back({"pip": ["torch @ " + WHEEL]})
    # the comment-bearing "torch == 2.0.0 # <url>" line is refused today, so the fallback line is written
    assert result == {"pip": ["torch # " + WHEEL]}


def test_pytorch_replace_back_for_a_plain_version_line():
    sub = _pytorch_substitution("torch", WHEEL)
    assert sub.replace_back({"pip": ["torch==2.0.0"]}) == {"pip": ["torch==2.0.0 # " + WHEEL]}


def test_the_resolver_builds_a_requirement_from_a_package_and_a_version_rule():
    req = MarkerRequirement(Requirement.parse("{} {}".format("torch", ">=1.0")))
    assert req.name == "torch"
    assert req.specs == [(">=", "1.0")]

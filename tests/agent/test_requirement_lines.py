import importlib.metadata
import os
import subprocess
import sys
import warnings

import pytest
from packaging.requirements import Requirement as PackagingRequirement
from packaging.version import Version

from cairnml_agent import definitions
from cairnml_agent.helper.package.requirement_lines import Requirement, parse


@pytest.fixture
def extra_indices():
    saved = list(definitions.PIP_EXTRA_INDICES)
    del definitions.PIP_EXTRA_INDICES[:]
    yield definitions.PIP_EXTRA_INDICES
    definitions.PIP_EXTRA_INDICES[:] = saved


def _declared():
    """The agent extra, as the installed cairnml distribution declares it."""
    lines = importlib.metadata.requires("cairnml") or []
    requirements = [PackagingRequirement(line) for line in lines]
    return {
        r.name: r
        for r in requirements
        if r.marker is not None and "extra" in str(r.marker) and r.marker.evaluate({"extra": "agent"})
    }


def test_the_dependency_is_declared():
    declared = _declared()
    assert "requirements-parser" in declared, "requirements-parser is not in requirements.txt"
    assert declared["requirements-parser"].specifier.contains("0.13.1")
    assert not declared["requirements-parser"].specifier.contains("0.14.0")
    assert "packaging" in declared
    assert not declared["packaging"].specifier.contains(Version("23.1"))
    assert declared["packaging"].specifier.contains(Version("23.2"))


def test_the_requirement_is_built_on_the_package_class():
    import requirements.requirement

    assert issubclass(Requirement, requirements.requirement.Requirement)
    assert "site-packages" in requirements.__file__ or "dist-packages" in requirements.__file__


def test_a_named_wheel_url_keeps_its_uri_and_is_not_a_specifier():
    req = Requirement.parse("name @ https://host/x/torch-2.0.0%2Bcu118-cp310-cp310-linux_x86_64.whl")
    assert req.name == "name"
    assert req.uri == "https://host/x/torch-2.0.0%2Bcu118-cp310-cp310-linux_x86_64.whl"
    assert req.specifier is False
    assert req.line == "name @ https://host/x/torch-2.0.0%2Bcu118-cp310-cp310-linux_x86_64.whl"


def test_a_named_file_url_is_a_local_file():
    req = Requirement.parse("name @ file:///x/pkg-1.0-py3-none-any.whl")
    assert (req.name, req.uri, req.local_file, req.specifier) == (
        "name",
        "file:///x/pkg-1.0-py3-none-any.whl",
        True,
        False,
    )


def test_a_named_vcs_url_keeps_the_url_as_its_line():
    req = Requirement.parse("name @ git+https://github.com/a/b.git@v1")
    assert (req.name, req.vcs, req.revision) == ("name", "git", "v1")
    assert req.line == "git+https://github.com/a/b.git@v1"


def test_a_named_vcs_url_with_extras_keeps_the_name_as_written():
    assert Requirement.parse("b[x] @ git+https://github.com/a/b.git").name == "b[x]"


@pytest.mark.parametrize("prefix", ["", "-e "])
def test_the_scp_form_of_a_git_url_is_a_vcs_requirement(prefix):
    req = Requirement.parse(prefix + "git+git@github.com:a/b.git@dev#egg=b")
    assert (req.vcs, req.revision, req.name) == ("git", "dev", "b")
    assert req.uri == "git+git://github.com:a/b.git"
    assert req.line == prefix + "git+git@github.com:a/b.git@dev#egg=b"
    assert req.editable is bool(prefix)


def test_an_editable_path_without_egg_keeps_the_whole_path():
    req = Requirement.parse("-e /abs/path")
    assert (req.path, req.name, req.local_file, req.editable) == ("/abs/path", None, True, True)
    assert req.line == "-e /abs/path"


def test_an_editable_relative_path_without_egg_keeps_the_whole_path():
    req = Requirement.parse("-e ./pkg")
    assert (req.path, req.name) == ("./pkg", None)


def test_an_editable_file_url_without_egg_drops_the_scheme():
    assert Requirement.parse("-e file:///abs/path").path == "/abs/path"


def test_an_editable_path_with_egg_names_the_package():
    req = Requirement.parse("-e /abs/path#egg=pk")
    assert (req.path, req.name) == ("/abs/path", "pk")


def test_a_comment_after_a_requirement_is_accepted():
    # new behaviour: the copy raised, the package accepts it (PytorchRequirement.replace_back relies on it)
    req = Requirement.parse("a == 1 # comment")
    assert req.name == "a"
    assert req.specs == [("==", "1")]


@pytest.mark.parametrize("line", ["--no-binary :all:", "-c cons.txt", "--pre", "# comment"])
def test_an_option_or_comment_line_raises(line):
    with pytest.raises(Exception):
        Requirement.parse(line)


def test_extras_come_back_sorted_whatever_the_hash_seed():
    code = (
        "from cairnml_agent.helper.package.requirement_lines import Requirement;"
        "print(Requirement.parse('x[Alpha,beta,gamma,delta]>=1').extras)"
    )
    outputs = set()
    for seed in ("1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
        assert done.returncode == 0, done.stderr
        outputs.add(done.stdout.strip())
    assert outputs == {"['alpha', 'beta', 'delta', 'gamma']"}


def test_a_list_of_lines_is_parsed():
    assert [r.name for r in parse(["numpy", "torch==1"])] == ["numpy", "torch"]


def test_a_list_with_an_r_line_resolves_it_against_cwd(tmp_path):
    (tmp_path / "inc.txt").write_text("scipy==1.1\n")
    # the copy raised UnboundLocalError here
    assert [r.name for r in parse(["numpy", "-r inc.txt"], cwd=str(tmp_path))] == ["numpy", "scipy"]


def test_an_r_line_resolves_next_to_a_named_file(tmp_path):
    (tmp_path / "inc.txt").write_text("scipy==1.1\n")
    main = tmp_path / "main.txt"
    main.write_text("numpy\n-r inc.txt\n")
    with open(main) as f:
        assert [r.name for r in parse(f)] == ["numpy", "scipy"]


def test_an_r_line_resolves_against_cwd_for_a_string(tmp_path):
    (tmp_path / "inc.txt").write_text("scipy==1.1\n")
    assert [r.name for r in parse("numpy\n-r inc.txt", cwd=str(tmp_path))] == ["numpy", "scipy"]


def test_an_r_line_to_a_missing_file_is_skipped(tmp_path):
    assert [r.name for r in parse("numpy\n-r nothing.txt\ntorch", cwd=str(tmp_path))] == ["numpy", "torch"]


def test_nothing_in_gives_nothing_out():
    assert list(parse("")) == []
    assert list(parse(None)) == []


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


def test_an_unknown_option_line_raises_so_callers_can_pass_it_through():
    with pytest.raises(Exception):
        list(parse("numpy\n--no-binary :all:"))


def test_the_manager_parses_a_line_with_a_trailing_comment_instead_of_passing_it_raw():
    from cairnml_agent.helper.package.requirements import RequirementsManager

    reqs = RequirementsManager.parse_requirements_section_to_marker_requirements(["a==1 # c"])
    assert reqs[0].name == "a"
    assert reqs[0].specs == [("==", "1")]

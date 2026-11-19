"""What the agent does with the top-level ``environment`` and ``files`` sections of its configuration.

These two functions are the agent's own and not the SDK's ``cairnml.backend_config.utils``: the SDK's apply_files has
no ``mode`` (documented in the agent's default configuration) and its apply_environment turns every false value
(0, False) into an empty string. The tests pin the agent's behaviour, so that a swap to the SDK's functions fails.
"""

import base64
import os
import stat

import pytest
from pyhocon import ConfigFactory

from cairnml_agent.backend_config.utils import apply_environment, apply_files


@pytest.fixture
def clean_environ(monkeypatch):
    for name in ("AGENT_TEST_A", "AGENT_TEST_B", "AGENT_TEST_C", "AGENT_TEST_D", "AGENT_TEST_HOME"):
        monkeypatch.delenv(name, raising=False)


def test_no_environment_section_applies_nothing(clean_environ):
    assert apply_environment(ConfigFactory.parse_string("a = 1")) == []


def test_the_environment_section_is_applied_and_its_keys_are_returned(clean_environ):
    config = ConfigFactory.parse_string('environment { AGENT_TEST_A: "x", AGENT_TEST_B: 12 }')

    assert apply_environment(config) == ["AGENT_TEST_A", "AGENT_TEST_B"]

    assert os.environ["AGENT_TEST_A"] == "x"
    assert os.environ["AGENT_TEST_B"] == "12"


def test_a_false_value_is_written_as_text_and_only_none_gives_an_empty_value(clean_environ):
    config = ConfigFactory.parse_string("environment { AGENT_TEST_A: false, AGENT_TEST_B: 0, AGENT_TEST_C: null }")

    apply_environment(config)

    assert os.environ["AGENT_TEST_A"] == "False"
    assert os.environ["AGENT_TEST_B"] == "0"
    assert os.environ["AGENT_TEST_C"] == ""


def test_an_environment_given_as_a_list_of_pairs_is_applied(clean_environ):
    class Config:
        def get(self, key, default=None):
            return [("AGENT_TEST_D", "pair")]

    assert apply_environment(Config()) == ["AGENT_TEST_D"]
    assert os.environ["AGENT_TEST_D"] == "pair"


def files_config(tmp_path, **files):
    entries = "\n".join("%s { %s }" % (key, body.replace("{TMP}", str(tmp_path))) for key, body in files.items())
    return ConfigFactory.parse_string("files {\n%s\n}" % entries)


def test_a_file_is_written_with_its_contents_and_reported(tmp_path, capsys):
    config = files_config(tmp_path, one='path: "{TMP}/sub/one.txt", contents: "hello"')

    apply_files(config)

    assert (tmp_path / "sub" / "one.txt").read_text() == "hello"
    out = capsys.readouterr().out
    assert "Creating files from configuration" in out
    assert "Saved [one]: %s" % (tmp_path / "sub" / "one.txt") in out


def test_no_files_section_prints_and_writes_nothing(capsys):
    apply_files(ConfigFactory.parse_string("a = 1"))

    assert capsys.readouterr().out == ""


def test_the_mode_of_a_file_is_applied_after_it_is_written(tmp_path):
    config = files_config(tmp_path, text='path: "{TMP}/a.sh", contents: "x", mode: "0o750"')

    apply_files(config)

    assert stat.S_IMODE((tmp_path / "a.sh").stat().st_mode) == 0o750


def test_an_integer_mode_is_read_as_octal_digits(tmp_path):
    apply_files(files_config(tmp_path, f='path: "{TMP}/c.sh", contents: "x", mode: 600'))

    assert stat.S_IMODE((tmp_path / "c.sh").stat().st_mode) == 0o600


def test_an_invalid_mode_is_reported_and_the_file_stays_written(tmp_path, capsys):
    apply_files(files_config(tmp_path, f='path: "{TMP}/d.sh", contents: "x", mode: "rwx"'))

    assert (tmp_path / "d.sh").read_text() == "x"
    assert "Skipped [f]: failed setting mode rwx" in capsys.readouterr().out


def test_a_base64_content_is_decoded_to_text_or_to_bytes(tmp_path):
    payload = base64.b64encode(b"secret").decode()
    config = files_config(
        tmp_path,
        text='path: "{TMP}/t.txt", format: base64, contents: "%s"' % payload,
        raw='path: "{TMP}/r.bin", format: base64, target_format: bytes, contents: "%s"' % payload,
    )

    apply_files(config)

    assert (tmp_path / "t.txt").read_text() == "secret"
    assert (tmp_path / "r.bin").read_bytes() == b"secret"


def test_a_content_that_is_not_base64_is_reported_and_skipped(tmp_path, capsys):
    apply_files(files_config(tmp_path, f='path: "{TMP}/x", format: base64, contents: "abc"'))

    assert not (tmp_path / "x").exists()
    assert "Skipped [f]: failed decoding base64" in capsys.readouterr().out


def test_json_and_yaml_targets_encode_the_contents(tmp_path):
    config = files_config(
        tmp_path,
        j='path: "{TMP}/o.json", target_format: json, contents: { a: 1 }',
        y='path: "{TMP}/o.yaml", target_format: yaml, contents: { a: 1 }',
    )

    apply_files(config)

    assert '"a": 1' in (tmp_path / "o.json").read_text()
    assert "a: 1" in (tmp_path / "o.yaml").read_text()


def test_a_tree_without_a_target_format_is_written_as_a_plain_dictionary(tmp_path):
    apply_files(files_config(tmp_path, f='path: "{TMP}/p.txt", contents: { a: 1 }'))

    assert (tmp_path / "p.txt").read_text().startswith("OrderedDict(")


def test_a_bytes_target_with_text_contents_is_a_clear_error_for_that_file(tmp_path, capsys):
    apply_files(files_config(tmp_path, f='path: "{TMP}/q.bin", target_format: bytes, contents: "text"'))

    out = capsys.readouterr().out
    assert "Skipped [f]: failed saving file" in out and "(a bytes-like object is required)" in out


def test_a_folder_and_an_existing_file_without_overwrite_are_skipped(tmp_path, capsys):
    (tmp_path / "kept.txt").write_text("old")
    (tmp_path / "folder").mkdir()
    config = files_config(
        tmp_path,
        kept='path: "{TMP}/kept.txt", contents: "new", overwrite: false',
        folder='path: "{TMP}/folder", contents: "new"',
    )

    apply_files(config)

    assert (tmp_path / "kept.txt").read_text() == "old"
    out = capsys.readouterr().out
    assert "Skipped [kept]: file exists" in out
    assert "Skipped [folder]: is a directory" in out


def test_an_existing_file_is_overwritten_by_default(tmp_path):
    (tmp_path / "f.txt").write_text("old")

    apply_files(files_config(tmp_path, f='path: "{TMP}/f.txt", contents: "new"'))

    assert (tmp_path / "f.txt").read_text() == "new"


def test_the_path_takes_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TEST_HOME", str(tmp_path))

    apply_files(files_config(tmp_path, f='path: "$AGENT_TEST_HOME/v.txt", contents: "v"'))

    assert (tmp_path / "v.txt").read_text() == "v"

"""Characterization of the steps every upload_artifact call shares, whatever the artifact type."""

import hashlib
import os
import threading

import pytest

from cairnml.backend_api import Session
from cairnml.backend_api.services import tasks
from cairnml.binding import artifacts as artifacts_module
from cairnml.utilities.proxy_object import LazyEvalWrapper


def test_an_old_server_refuses_the_upload_and_returns_false(artifacts_env, monkeypatch):
    monkeypatch.setattr(Session, "check_min_api_version", classmethod(lambda cls, version: False))
    assert artifacts_env.upload("a", {"k": 1}) is False
    assert artifacts_env.uploads == []
    assert artifacts_env.log.warnings == [
        "Artifacts not supported by your CairnML-server version, please upgrade to the latest server version"
    ]


def test_the_minimum_api_version_asked_for_is_2_3(artifacts_env, monkeypatch):
    asked = []
    caller = threading.get_ident()

    def check(cls, version):
        # background threads of other tests may ask too: only this thread counts
        if threading.get_ident() == caller:
            asked.append(version)
        return True

    monkeypatch.setattr(Session, "check_min_api_version", classmethod(check))
    artifacts_env.upload("a", {"k": 1})
    # the API proxy also asks for later versions on this thread; the upload must ask for 2.3
    assert "2.3" in asked


def test_a_name_that_is_already_registered_raises(artifacts_env):
    dict.__setitem__(artifacts_env.manager._artifacts_container, "dup", object())
    with pytest.raises(ValueError) as error:
        artifacts_env.upload("dup", {"k": 1})
    assert str(error.value) == "Artifact by the name of dup is already registered, use register_artifact"
    assert artifacts_env.uploads == []


def test_the_result_carries_every_field_of_the_artifact(artifacts_env):
    assert artifacts_env.upload("cfg", {"b": 1, "a": 2}) is True
    entry = artifacts_env.artifact
    data = artifacts_env.uploads[0]["data"]
    assert entry.key == "cfg"
    assert entry.type == "dict"
    assert entry.uri == "s3://bucket/cfg/cfg.json"
    assert entry.content_size == len(data)
    assert entry.hash == hashlib.sha256(data).hexdigest()
    assert entry.type_data.content_type == "application/json"
    assert entry.type_data.preview == data.decode()
    assert entry.display_data is None
    assert isinstance(entry.timestamp, int)
    assert data.decode() == '{\n    "a": 2,\n    "b": 1\n}'
    assert artifacts_env.leftovers() == []


def test_the_upload_arguments_reach_the_uploader(artifacts_env):
    artifacts_env.upload("cfg", {"a": 1}, wait_on_upload=True)
    upload = artifacts_env.uploads[0]
    assert upload["name"] == "cfg"
    assert upload["wait_on_upload"] is True
    assert upload["delete_after_upload"] is True
    assert upload["override_filename"] == "cfg.json"
    assert upload["override_filename_ext"] == ".json"


def test_wait_on_upload_is_off_by_default(artifacts_env):
    artifacts_env.upload("cfg", {"a": 1})
    assert artifacts_env.uploads[0]["wait_on_upload"] is False


def test_delete_after_upload_reaches_the_uploader_for_a_user_file(artifacts_env, tmp_path):
    source = tmp_path / "keep.bin"
    source.write_bytes(b"abc")
    artifacts_env.upload("f", source, delete_after_upload=True)
    assert artifacts_env.uploads[0]["delete_after_upload"] is True
    assert not source.exists()


def test_a_user_file_is_not_deleted_by_default(artifacts_env, tmp_path):
    source = tmp_path / "keep.bin"
    source.write_bytes(b"abc")
    artifacts_env.upload("f", source)
    assert artifacts_env.uploads[0]["delete_after_upload"] is False
    assert source.exists()


def test_metadata_becomes_display_data_as_strings(artifacts_env):
    artifacts_env.upload("cfg", {"a": 1}, metadata={"epochs": 3, 4: "x"})
    assert artifacts_env.artifact.display_data == [("epochs", "3"), ("4", "x")]


def test_empty_metadata_gives_no_display_data(artifacts_env):
    artifacts_env.upload("cfg", {"a": 1}, metadata={})
    assert artifacts_env.artifact.display_data is None


def test_a_preview_is_cast_to_a_string(artifacts_env):
    artifacts_env.upload("cfg", {"a": 1}, preview=123)
    assert artifacts_env.artifact.type_data.preview == "123"


def test_a_preview_of_false_is_kept_as_false_and_replaced_by_the_default(artifacts_env):
    artifacts_env.upload("cfg", {"a": 1}, preview=False)
    # False is kept as False (not cast), then `preview or serialized_text` picks the text
    assert artifacts_env.artifact.type_data.preview == '{\n    "a": 1\n}'


def test_a_lazy_object_is_evaluated_before_the_upload(artifacts_env, monkeypatch):
    wrapper = LazyEvalWrapper(callback=lambda: {"lazy": True})
    artifacts_env.upload("lz", wrapper)
    assert artifacts_env.artifact.type == "dict"
    assert artifacts_env.uploads[0]["data"].decode() == '{\n    "lazy": true\n}'


def test_a_lazy_object_is_loaded_once_and_its_value_is_what_gets_uploaded(artifacts_env):
    calls = []

    def produce():
        calls.append(1)
        return "lazy text"

    artifacts_env.upload("lz", LazyEvalWrapper(callback=produce))
    assert artifacts_env.artifact.type == "string"
    assert artifacts_env.uploads[0]["data"] == b"lazy text"


def test_an_oversize_preview_is_cut_to_the_limit(artifacts_env):
    limit = artifacts_env.manager.max_preview_size_bytes
    artifacts_env.upload("big", object(), preview="p" * (limit + 2000), auto_pickle=True)
    preview = artifacts_env.artifact.type_data.preview
    assert preview.startswith(f"# full preview too large to store, storing first {limit // 1024}kb\n")
    assert preview.endswith("p" * limit)
    assert len(preview) == len(f"# full preview too large to store, storing first {limit // 1024}kb\n") + limit


def test_a_preview_just_inside_the_slack_is_kept(artifacts_env):
    limit = artifacts_env.manager.max_preview_size_bytes
    artifacts_env.upload("big", object(), preview="p" * (limit + 1024), auto_pickle=True)
    assert artifacts_env.artifact.type_data.preview == "p" * (limit + 1024)


def test_an_existing_entry_of_the_same_key_is_replaced(artifacts_env):
    artifacts_env.upload("cfg", {"a": 1})
    artifacts_env.upload("cfg", {"a": 2})
    assert len(artifacts_env.artifacts) == 1
    assert artifacts_env.artifact.hash == hashlib.sha256(artifacts_env.uploads[1]["data"]).hexdigest()
    assert len(artifacts_env.uploads) == 2


def test_other_keys_stay_beside_the_new_entry(artifacts_env):
    artifacts_env.upload("one", {"a": 1})
    artifacts_env.upload("two", {"a": 2})
    assert [a.key for a in artifacts_env.artifacts] == ["one", "two"]


def test_an_existing_data_audit_entry_of_the_same_key_raises(artifacts_env):
    artifacts_env.manager._task_artifact_list.append(tasks.Artifact(key="tbl", type="data-audit-table"))
    with pytest.raises(ValueError) as error:
        artifacts_env.upload("tbl", {"a": 1})
    assert str(error.value) == "Artifact of name tbl already registered, use register_artifact instead"
    assert artifacts_env.uploads == []
    assert len(artifacts_env.manager._task_artifact_list) == 1


def test_a_missing_local_file_returns_false(artifacts_env):
    def serialize_and_lose_the_file(obj):
        for path in artifacts_env.tmp_dir.rglob("*.bin"):
            os.unlink(str(path))
        return b"data"

    assert (
        artifacts_env.upload(
            "gone", {"a": 1}, serialization_function=serialize_and_lose_the_file, extension_name=".bin"
        )
        is False
    )
    assert artifacts_env.uploads == []
    assert artifacts_env.log.warnings[-1].startswith("Artifact upload failed, cannot find file ")
    assert artifacts_env.task.added == []


def test_auto_pickle_defaults_to_the_configured_value_which_is_on(artifacts_env):
    assert artifacts_env.upload("obj", object()) is True
    assert artifacts_env.artifact.type == "pickle"


def test_auto_pickle_follows_the_configuration_when_it_is_switched_off(artifacts_env, monkeypatch):
    asked = []

    def configured(key, default=None):
        asked.append(key)
        return False

    monkeypatch.setattr(artifacts_module.config, "get", configured)
    with pytest.raises(ValueError):
        artifacts_env.upload("obj", object())
    assert "development.artifacts.auto_pickle" in asked


def test_without_auto_pickle_an_unknown_object_is_refused(artifacts_env):
    with pytest.raises(ValueError) as error:
        artifacts_env.upload("obj", object(), auto_pickle=False)
    assert str(error.value) == "Artifact type <class 'object'> not supported"
    assert artifacts_env.uploads == []
    assert artifacts_env.leftovers() == []


def test_a_preview_one_character_past_the_slack_is_cut(artifacts_env):
    limit = artifacts_env.manager.max_preview_size_bytes
    artifacts_env.upload("big", object(), preview="p" * (limit + 1025), auto_pickle=True)
    assert artifacts_env.artifact.type_data.preview.startswith("# full preview too large")

"""Characterization of upload_artifact, one test per artifact type and per fallback."""

import gzip
import hashlib
import io
import json
import pathlib
import pickle
import zipfile

import numpy as np
import pytest
import yaml
from PIL import Image

from cairnml.binding import artifacts as artifacts_module
from cairnml.storage.size import format_size


class FakeMultiIndex:
    pass


class FakeFrame:
    """Stand-in for pandas.DataFrame: pandas is not a dependency of the SDK."""

    fail_on = ()

    def __init__(self, index=None, columns=None):
        self.index = index
        self.columns = columns

    def __repr__(self):
        return "FakeFrame(2x2)"

    def to_csv(self, handle, **kwargs):
        handle.write(b"a,b\n1,2\n")

    def to_parquet(self, path):
        if "parquet" in self.fail_on:
            raise RuntimeError("no parquet engine")
        pathlib.Path(path).write_bytes(b"PARQUET")

    def to_feather(self, path):
        if "feather" in self.fail_on:
            raise RuntimeError("no feather engine")
        pathlib.Path(path).write_bytes(b"FEATHER")

    def to_pickle(self, path):
        pathlib.Path(path).write_bytes(b"PICKLE")


class FakePandas:
    DataFrame = FakeFrame
    MultiIndex = FakeMultiIndex
    __version__ = "2.1.0"


@pytest.fixture
def pandas_stand_in(monkeypatch):
    monkeypatch.setattr(artifacts_module, "pd", FakePandas)
    monkeypatch.setattr(FakeFrame, "fail_on", ())
    return FakePandas


def check(env, *, type, uri_name, content_type, preview, ext, override, delete, data=None):
    """Assert the complete observable result of the last upload and return the uploaded bytes."""
    upload = env.uploads[-1]
    entry = env.artifact
    assert entry.type == type
    assert entry.uri == "s3://bucket/{}/{}".format(upload["name"], uri_name)
    assert entry.type_data.content_type == content_type
    assert entry.type_data.preview == preview
    assert upload["override_filename_ext"] == ext
    assert upload["override_filename"] == override
    assert upload["delete_after_upload"] is delete
    assert entry.content_size == len(upload["data"])
    assert entry.hash == hashlib.sha256(upload["data"]).hexdigest()
    if data is not None:
        assert upload["data"] == data
    return upload["data"]


# ---- custom serialization function -------------------------------------------------


def test_custom_serialization_function(artifacts_env):
    assert artifacts_env.upload("c", {"a": 1}, serialization_function=lambda o: b"RAW", extension_name=".bin")
    check(
        artifacts_env,
        type="custom",
        uri_name="c.bin",
        content_type="application/octet-stream",
        preview="{'a': 1}",
        ext=".bin",
        override="c.bin",
        delete=False,
        data=b"RAW",
    )


def test_custom_serialization_function_without_extension(artifacts_env):
    artifacts_env.upload(
        "c", object(), serialization_function=lambda o: b"RAW", preview="mine", delete_after_upload=True
    )
    check(
        artifacts_env,
        type="custom",
        uri_name="c",
        content_type=None,
        preview="mine",
        ext="",
        override="c",
        delete=True,
        data=b"RAW",
    )


def test_custom_serialization_function_with_an_unprintable_object_has_an_empty_preview(artifacts_env):
    artifacts_env.upload("c", _BrokenRepr(), serialization_function=lambda o: b"RAW", delete_after_upload=True)
    assert artifacts_env.artifact.type_data.preview == ""


def test_custom_serialization_function_wins_over_the_object_type(artifacts_env):
    artifacts_env.upload("c", {"a": 1}, serialization_function=lambda o: bytearray(b"X"), delete_after_upload=True)
    assert artifacts_env.artifact.type == "custom"
    assert artifacts_env.uploads[0]["data"] == b"X"


def test_a_failing_serialization_function_raises_and_leaves_no_temp_file(artifacts_env):
    def explode(obj):
        raise RuntimeError("cannot serialize")

    with pytest.raises(RuntimeError, match="cannot serialize"):
        artifacts_env.upload("c", {"a": 1}, serialization_function=explode)
    assert artifacts_env.uploads == []
    assert artifacts_env.task.added == []
    assert artifacts_env.leftovers() == []


# ---- pickle ------------------------------------------------------------------------


def test_pkl_extension_pickles_any_object(artifacts_env):
    artifacts_env.upload("p", {"a": [1, 2]}, extension_name=".pkl")
    data = check(
        artifacts_env,
        type="pickle",
        uri_name="p.pkl",
        content_type="application/pickle",
        preview="{'a': [1, 2]}",
        ext=".pkl",
        override="p.pkl",
        delete=True,
    )
    assert pickle.loads(data) == {"a": [1, 2]}
    assert artifacts_env.leftovers() == []


def test_none_is_pickled(artifacts_env):
    artifacts_env.upload("n", None)
    data = check(
        artifacts_env,
        type="pickle",
        uri_name="n.pkl",
        content_type="application/pickle",
        preview="None",
        ext=".pkl",
        override="n.pkl",
        delete=True,
    )
    assert pickle.loads(data) is None


def test_an_empty_string_is_pickled(artifacts_env):
    artifacts_env.upload("e", "")
    data = check(
        artifacts_env,
        type="pickle",
        uri_name="e.pkl",
        content_type="application/pickle",
        preview="''",
        ext=".pkl",
        override="e.pkl",
        delete=True,
    )
    assert pickle.loads(data) == ""


class _Opaque:
    def __init__(self):
        self.value = 7

    def __repr__(self):
        return "Opaque(7)"


class _BrokenRepr:
    def __repr__(self):
        raise RuntimeError("no repr")


def test_an_unknown_object_is_pickled_with_auto_pickle(artifacts_env):
    artifacts_env.upload("o", _Opaque(), auto_pickle=True)
    data = check(
        artifacts_env,
        type="pickle",
        uri_name="o.pkl",
        content_type="application/pickle",
        preview="Opaque(7)",
        ext=".pkl",
        override="o.pkl",
        delete=True,
    )
    assert pickle.loads(data).value == 7


def test_pickle_preview_falls_back_to_empty_when_repr_fails(artifacts_env):
    artifacts_env.upload("b", _BrokenRepr(), extension_name=".pkl", preview=None)
    assert artifacts_env.artifact.type_data.preview == ""


def test_a_failing_pickle_dump_raises_and_leaves_no_temp_file(artifacts_env):
    with pytest.raises(Exception):
        artifacts_env.upload("u", lambda: None, extension_name=".pkl")
    assert artifacts_env.uploads == []
    assert artifacts_env.leftovers() == []


# ---- numpy -------------------------------------------------------------------------


def test_numpy_array_is_stored_as_npz_by_default(artifacts_env):
    array = np.arange(4).reshape(2, 2)
    artifacts_env.upload("arr", array)
    data = check(
        artifacts_env,
        type="numpy",
        uri_name="arr.npz",
        content_type="application/numpy",
        preview=repr(array),
        ext=".npz",
        override="arr.npz",
        delete=True,
    )
    assert np.array_equal(np.load(io.BytesIO(data))["arr"], array)
    assert artifacts_env.leftovers() == []


def test_numpy_array_as_csv_gz(artifacts_env):
    array = np.array([[1.0, 2.0], [3.0, 4.0]])
    artifacts_env.upload("arr", array, extension_name=".csv.gz", preview="mine")
    data = check(
        artifacts_env,
        type="numpy",
        uri_name="arr.csv.gz",
        content_type="text/csv",
        preview="mine",
        ext=".csv.gz",
        override="arr.csv.gz",
        delete=True,
    )
    assert gzip.decompress(data).decode().split() == [
        "1.000000000000000000e+00,2.000000000000000000e+00",
        "3.000000000000000000e+00,4.000000000000000000e+00",
    ]


def test_numpy_array_with_an_invalid_extension_falls_back_to_npz_with_a_warning(artifacts_env):
    artifacts_env.upload("arr", np.zeros(2), extension_name=".foo")
    assert artifacts_env.artifact.uri.endswith("arr.npz")
    assert artifacts_env.log.warnings == [
        "numpy artifact can not be uploaded with extension .foo. Valid extensions are: .npz, .csv.gz. "
        "Defaulting to .npz."
    ]


# ---- pandas (stand-in) -------------------------------------------------------------


def test_pandas_frame_defaults_to_csv_gz(artifacts_env, pandas_stand_in):
    artifacts_env.upload("df", FakeFrame())
    data = check(
        artifacts_env,
        type="pandas",
        uri_name="df",
        content_type="text/csv",
        preview="FakeFrame(2x2)",
        ext=".csv.gz",
        override="df",
        delete=True,
    )
    assert gzip.decompress(data) == b"a,b\n1,2\n"
    assert artifacts_env.leftovers() == []


@pytest.mark.parametrize(
    "ext, content_type, payload",
    [
        (".parquet", "application/parquet", b"PARQUET"),
        (".feather", "application/feather", b"FEATHER"),
        (".pickle", "application/pickle", b"PICKLE"),
    ],
)
def test_pandas_frame_with_an_explicit_extension(artifacts_env, pandas_stand_in, ext, content_type, payload):
    artifacts_env.upload("df", FakeFrame(), extension_name=ext)
    check(
        artifacts_env,
        type="pandas",
        uri_name="df",
        content_type=content_type,
        preview="FakeFrame(2x2)",
        ext=ext,
        override="df",
        delete=True,
        data=payload,
    )


@pytest.mark.parametrize("fmt", ["parquet", "feather"])
def test_pandas_frame_falls_back_to_csv_gz_when_the_writer_fails(artifacts_env, pandas_stand_in, monkeypatch, fmt):
    monkeypatch.setattr(FakeFrame, "fail_on", (fmt,))
    artifacts_env.upload("df", FakeFrame(), extension_name="." + fmt)
    data = check(
        artifacts_env,
        type="pandas",
        uri_name="df",
        content_type="text/csv",
        preview="FakeFrame(2x2)",
        ext="." + fmt,
        override="df",
        delete=True,
    )
    assert gzip.decompress(data) == b"a,b\n1,2\n"
    assert artifacts_env.log.warnings == [
        "Exception 'no {} engine' encountered when uploading artifact as .{}. Defaulting to .csv.gz".format(fmt, fmt)
    ]


def test_pandas_frame_with_an_invalid_extension_falls_back_to_csv_gz(artifacts_env, pandas_stand_in):
    artifacts_env.upload("df", FakeFrame(), extension_name=".xls")
    assert artifacts_env.uploads[0]["override_filename_ext"] == ".csv.gz"
    assert artifacts_env.log.warnings == [
        "pandas artifact can not be uploaded with extension .xls. "
        "Valid extensions are: .csv.gz, .parquet, .feather, .pickle. Defaulting to .csv.gz."
    ]


def test_pandas_frame_takes_its_default_extension_from_the_configuration(artifacts_env, pandas_stand_in, monkeypatch):
    monkeypatch.setattr(artifacts_module.Artifacts, "_default_pandas_dataframe_extension_name", ".parquet")
    artifacts_env.upload("df", FakeFrame())
    check(
        artifacts_env,
        type="pandas",
        uri_name="df",
        content_type="application/parquet",
        preview="FakeFrame(2x2)",
        ext=".parquet",
        override="df",
        delete=True,
        data=b"PARQUET",
    )


@pytest.mark.parametrize("where", ["index", "columns"])
def test_pandas_frame_with_a_multi_index_is_pickled_when_no_extension_is_given(artifacts_env, pandas_stand_in, where):
    frame = FakeFrame(**{where: FakeMultiIndex()})
    artifacts_env.upload("df", frame)
    data = check(
        artifacts_env,
        type="pickle",
        uri_name="df.pkl",
        content_type="application/pickle",
        preview="FakeFrame(2x2)",
        ext=".pkl",
        override="df.pkl",
        delete=True,
    )
    assert isinstance(pickle.loads(data), FakeFrame)


def test_a_multi_index_frame_pickle_leaves_the_unused_csv_temp_file_behind(artifacts_env, pandas_stand_in):
    """Pins a latent defect: the temp folder prepared for the frame is abandoned on the pickle fallback."""
    artifacts_env.upload("df", FakeFrame(index=FakeMultiIndex()))
    left = artifacts_env.leftovers()
    assert len(left) == 1 and left[0].startswith("artifacts_")


def test_pandas_frame_with_a_multi_index_and_an_explicit_extension_is_written_in_that_format(
    artifacts_env, pandas_stand_in
):
    artifacts_env.upload("df", FakeFrame(index=FakeMultiIndex()), extension_name=".parquet")
    assert artifacts_env.artifact.type == "pandas"
    assert artifacts_env.uploads[0]["data"] == b"PARQUET"


def test_pandas_frame_with_a_multi_index_and_a_configured_extension_is_not_pickled(
    artifacts_env, pandas_stand_in, monkeypatch
):
    monkeypatch.setattr(artifacts_module.Artifacts, "_default_pandas_dataframe_extension_name", ".feather")
    artifacts_env.upload("df", FakeFrame(columns=FakeMultiIndex()))
    assert artifacts_env.artifact.type == "pandas"
    assert artifacts_env.uploads[0]["data"] == b"FEATHER"


# ---- image -------------------------------------------------------------------------


def _image():
    return Image.new("RGB", (4, 4), (10, 20, 30))


def _image_that_saves_without_plugins():
    image = _image()
    image.save = lambda path: pathlib.Path(path).write_bytes(b"PNGDATA")
    return image


def test_image_is_stored_as_png_by_default(artifacts_env):
    artifacts_env.upload("img", _image())
    data = check(
        artifacts_env,
        type="image",
        uri_name="img.png",
        content_type="image/png",
        preview="PIL.Image.Image image mode=RGB size=4x4",
        ext=".png",
        override="img.png",
        delete=True,
    )
    assert Image.open(io.BytesIO(data)).format == "PNG"
    assert artifacts_env.leftovers() == []


def test_image_with_another_valid_extension(artifacts_env):
    artifacts_env.upload("img", _image(), extension_name=".jpg", preview="mine")
    data = check(
        artifacts_env,
        type="image",
        uri_name="img.jpg",
        content_type="image/jpeg",
        preview="mine",
        ext=".jpg",
        override="img.jpg",
        delete=True,
    )
    assert Image.open(io.BytesIO(data)).format == "JPEG"


def test_image_with_an_invalid_extension_falls_back_to_png_with_a_warning(artifacts_env):
    Image.init()
    artifacts_env.upload("img", _image(), extension_name=".xyz")
    assert artifacts_env.artifact.uri.endswith("img.png")
    assert artifacts_env.log.warnings == [
        "image artifact can not be uploaded with extension .xyz. Valid extensions are: {}. Defaulting to .png.".format(
            ", ".join(Image.EXTENSION.keys())
        )
    ]


def test_image_with_an_empty_extension_table_uses_png_and_warns_only_for_another_extension(artifacts_env, monkeypatch):
    monkeypatch.setattr(Image, "EXTENSION", {})
    monkeypatch.setattr(Image, "init", lambda: None)
    artifacts_env.upload("img", _image_that_saves_without_plugins(), extension_name=".png")
    artifacts_env.upload("img2", _image_that_saves_without_plugins())
    assert artifacts_env.log.warnings == []
    artifacts_env.upload("img3", _image_that_saves_without_plugins(), extension_name=".jpg")
    assert artifacts_env.log.warnings == ["image artifact can not be uploaded with extension .jpg. Defaulting to .png."]
    assert artifacts_env.uploads[-1]["override_filename_ext"] == ".png"


def test_image_when_the_plugin_initialisation_fails_uses_png(artifacts_env, monkeypatch):
    def broken_init():
        raise RuntimeError("no plugins")

    monkeypatch.setattr(Image, "EXTENSION", {})
    monkeypatch.setattr(Image, "init", broken_init)
    artifacts_env.upload("img", _image_that_saves_without_plugins(), extension_name=".gif")
    assert artifacts_env.uploads[0]["override_filename_ext"] == ".png"
    assert artifacts_env.log.warnings == ["image artifact can not be uploaded with extension .gif. Defaulting to .png."]


# ---- dict --------------------------------------------------------------------------


def test_dict_is_stored_as_json_with_sorted_keys(artifacts_env):
    artifacts_env.upload("d", {"b": 1, "a": {"x": [1, 2]}})
    text = json.dumps({"b": 1, "a": {"x": [1, 2]}}, sort_keys=True, indent=4)
    data = check(
        artifacts_env,
        type="dict",
        uri_name="d.json",
        content_type="application/json",
        preview=text,
        ext=".json",
        override="d.json",
        delete=True,
    )
    assert data.decode() == text


def test_dict_keeps_the_key_order_when_sort_keys_is_off(artifacts_env):
    artifacts_env.upload("d", {"b": 1, "a": 2}, sort_keys=False)
    assert artifacts_env.uploads[0]["data"].decode().index('"b"') < artifacts_env.uploads[0]["data"].decode().index(
        '"a"'
    )


def test_dict_as_yaml(artifacts_env):
    artifacts_env.upload("d", {"b": 1, "a": 2}, extension_name=".yaml")
    text = yaml.dump({"b": 1, "a": 2}, sort_keys=True, indent=4)
    check(
        artifacts_env,
        type="dict",
        uri_name="d.yaml",
        content_type="application/yaml",
        preview=text,
        ext=".yaml",
        override="d.yaml",
        delete=True,
        data=text.encode(),
    )
    assert text.startswith("a: 2")


def test_yaml_keeps_the_key_order_when_sort_keys_is_off(artifacts_env):
    artifacts_env.upload("d", {"b": 1, "a": 2}, extension_name=".yaml", sort_keys=False)
    assert artifacts_env.uploads[0]["data"].decode().startswith("b: 1")


def test_image_with_an_extension_that_has_no_mime_type_gets_the_unknown_type(artifacts_env):
    artifacts_env.upload("img", _image_that_saves_without_plugins(), extension_name=".dds")
    assert artifacts_env.artifact.type_data.content_type == "image/unknown-type"


def test_dict_with_an_invalid_extension_falls_back_to_json(artifacts_env):
    artifacts_env.upload("d", {"a": 1}, extension_name=".toml")
    assert artifacts_env.uploads[0]["override_filename_ext"] == ".json"
    assert artifacts_env.log.warnings == [
        "dict artifact can not be uploaded with extension .toml. Valid extensions are: .json, .yaml. Defaulting to .json."
    ]


def test_dict_preview_is_the_users_preview_when_given(artifacts_env):
    artifacts_env.upload("d", {"a": 1}, preview="mine")
    assert artifacts_env.artifact.type_data.preview == "mine"


def test_a_large_dict_preview_is_cut(artifacts_env):
    limit = artifacts_env.manager.max_preview_size_bytes
    value = {"k": "v" * (limit + 10)}
    artifacts_env.upload("d", value)
    text = json.dumps(value, sort_keys=True, indent=4)
    assert artifacts_env.artifact.type_data.preview == (
        "# full serialized dict too large to store, storing first {}kb\n{}".format(limit // 1024, text[:limit])
    )
    assert artifacts_env.uploads[0]["data"].decode() == text


MIXED_KEYS = {1: "a", "b": 2}


def test_json_failure_without_auto_pickle_raises_and_leaves_no_temp_file(artifacts_env):
    with pytest.raises(TypeError):
        artifacts_env.upload("d", MIXED_KEYS, auto_pickle=False)
    assert artifacts_env.uploads == []
    assert artifacts_env.leftovers() == []


def test_json_failure_with_auto_pickle_reverts_to_pickle(artifacts_env):
    artifacts_env.upload("d", MIXED_KEYS, auto_pickle=True)
    data = check(
        artifacts_env,
        type="pickle",
        uri_name="d.pkl",
        content_type="application/pickle",
        preview=repr(MIXED_KEYS),
        ext=".pkl",
        override="d.pkl",
        delete=True,
    )
    assert pickle.loads(data) == MIXED_KEYS
    assert artifacts_env.log.warnings == ["JSON serialization of artifact 'd' failed, reverting to pickle"]


@pytest.fixture
def broken_yaml(monkeypatch):
    def dump(*args, **kwargs):
        raise RuntimeError("yaml broke")

    monkeypatch.setattr(artifacts_module.yaml, "dump", dump)


def test_yaml_failure_without_auto_pickle_raises(artifacts_env, broken_yaml):
    with pytest.raises(RuntimeError, match="yaml broke"):
        artifacts_env.upload("d", MIXED_KEYS, extension_name=".yaml", auto_pickle=False)
    assert artifacts_env.uploads == []
    assert artifacts_env.leftovers() == []


def test_yaml_failure_with_auto_pickle_reverts_to_pickle(artifacts_env, broken_yaml):
    artifacts_env.upload("d", MIXED_KEYS, extension_name=".yaml", auto_pickle=True)
    assert artifacts_env.artifact.type == "pickle"
    assert artifacts_env.artifact.type_data.content_type == "application/pickle"
    assert artifacts_env.log.warnings == ["YAML serialization of artifact 'd' failed, reverting to pickle"]


def test_a_mixed_key_dict_is_stored_as_json_when_sort_keys_is_off(artifacts_env):
    artifacts_env.upload("d", MIXED_KEYS, sort_keys=False)
    assert artifacts_env.artifact.type == "dict"


# ---- single file, folder, wildcard, list of paths ---------------------------------


def _tree(tmp_path):
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("alpha")
    (root / "sub" / "b.txt").write_text("bravo!")
    return root


def _zip_members(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {n: archive.read(n) for n in archive.namelist()}


def test_a_single_file_is_uploaded_as_it_is(artifacts_env, tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("hello")
    artifacts_env.upload("f", pathlib.Path(source))
    check(
        artifacts_env,
        type="custom",
        uri_name="notes.txt",
        content_type="text/plain",
        preview="notes.txt - {}\n".format(format_size(5)),
        ext=None,
        override="notes.txt",
        delete=False,
        data=b"hello",
    )


def test_a_single_file_given_as_a_string_is_uploaded_as_a_file(artifacts_env, tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("hello")
    artifacts_env.upload("f", str(source), preview="mine")
    assert artifacts_env.artifact.type == "custom"
    assert artifacts_env.artifact.type_data.preview == "mine"
    assert artifacts_env.uploads[0]["data"] == b"hello"


def test_a_folder_is_zipped(artifacts_env, tmp_path):
    root = _tree(tmp_path)
    artifacts_env.upload("d", pathlib.Path(root))
    data = artifacts_env.uploads[0]["data"]
    assert _zip_members(data) == {"a.txt": b"alpha", "sub/b.txt": b"bravo!"}
    check(
        artifacts_env,
        type="archive",
        uri_name="tree.zip",
        content_type="application/zip",
        preview="Archive content {}/*:\na.txt - {}\nsub/b.txt - {}\n".format(
            root.as_posix(), format_size(5), format_size(6)
        ),
        ext=".zip",
        override="tree.zip",
        delete=True,
    )
    assert artifacts_env.leftovers() == []


def test_a_wildcard_string_is_zipped(artifacts_env, tmp_path):
    root = _tree(tmp_path)
    artifacts_env.upload("w", str(root / "*.txt"))
    assert _zip_members(artifacts_env.uploads[0]["data"]) == {"a.txt": b"alpha", "sub/b.txt": b"bravo!"}
    assert artifacts_env.artifact.type == "archive"
    assert artifacts_env.artifact.type_data.preview.startswith("Archive content {}/*.txt:\n".format(root.as_posix()))
    assert artifacts_env.uploads[0]["override_filename"] == "tree.zip"


def test_a_path_in_a_missing_folder_raises(artifacts_env, tmp_path):
    missing = pathlib.Path(tmp_path / "nowhere" / "file.txt")
    with pytest.raises(ValueError) as error:
        artifacts_env.upload("m", missing)
    assert str(error.value) == "Artifact file/folder '{}' could not be found".format(missing.as_posix())
    assert artifacts_env.uploads == []


def test_a_missing_file_in_an_existing_folder_gives_an_empty_archive(artifacts_env, tmp_path):
    """Pins today's behaviour: the missing file is not reported, an empty zip is uploaded."""
    artifacts_env.upload("m", pathlib.Path(tmp_path / "nofile.txt"))
    assert artifacts_env.artifact.type == "archive"
    assert _zip_members(artifacts_env.uploads[0]["data"]) == {}


def test_a_zip_failure_for_a_folder_returns_false(artifacts_env, tmp_path, monkeypatch):
    root = _tree(tmp_path)

    def broken_zip(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(artifacts_module, "ZipFile", broken_zip)
    assert artifacts_env.upload("d", pathlib.Path(root)) is False
    assert artifacts_env.uploads == []
    assert artifacts_env.task.added == []
    assert artifacts_env.log.warnings == ["Exception {}\nFailed zipping artifact folder disk full".format(root)]


def test_a_list_of_paths_is_zipped_relative_to_the_common_folder(artifacts_env, tmp_path):
    root = _tree(tmp_path)
    paths = [pathlib.Path(root / "a.txt"), pathlib.Path(root / "sub" / "b.txt")]
    artifacts_env.upload("l", paths)
    check(
        artifacts_env,
        type="archive",
        uri_name="l.zip",
        content_type="application/zip",
        preview="Archive content:\na.txt - {}\nsub/b.txt - {}\n".format(format_size(5), format_size(6)),
        ext=".zip",
        override="l.zip",
        delete=True,
    )
    assert _zip_members(artifacts_env.uploads[0]["data"]) == {"a.txt": b"alpha", "sub/b.txt": b"bravo!"}
    assert artifacts_env.leftovers() == []


def test_a_tuple_of_paths_is_zipped_too(artifacts_env, tmp_path):
    root = _tree(tmp_path)
    artifacts_env.upload("t", (pathlib.Path(root / "a.txt"),))
    assert artifacts_env.artifact.type == "archive"
    assert list(_zip_members(artifacts_env.uploads[0]["data"])) != []


def test_a_missing_member_of_a_list_is_skipped_with_a_warning(artifacts_env, tmp_path):
    root = _tree(tmp_path)
    ghost = pathlib.Path(root / "ghost.txt")
    artifacts_env.upload("l", [pathlib.Path(root / "a.txt"), ghost])
    assert _zip_members(artifacts_env.uploads[0]["data"]) == {"a.txt": b"alpha"}
    assert artifacts_env.log.warnings == ["Failed zipping artifact file '{}', file not found!".format(ghost.as_posix())]


def test_a_zip_failure_for_a_list_returns_false(artifacts_env, tmp_path, monkeypatch):
    root = _tree(tmp_path)
    paths = [pathlib.Path(root / "a.txt")]

    def broken_zip(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(artifacts_module, "ZipFile", broken_zip)
    assert artifacts_env.upload("l", paths) is False
    assert artifacts_env.uploads == []
    assert artifacts_env.log.warnings == ["Exception {}\nFailed zipping artifact files disk full".format(paths)]


def test_a_list_that_is_not_all_paths_is_not_zipped(artifacts_env, tmp_path):
    artifacts_env.upload("l", [1, 2], auto_pickle=True)
    assert artifacts_env.artifact.type == "pickle"


# ---- strings and remote uris -------------------------------------------------------


@pytest.mark.parametrize("uri", ["s3://bucket/data.csv", "https://example.com/data.csv", "gs://bucket/data.csv"])
def test_a_remote_uri_is_registered_and_not_uploaded(artifacts_env, uri):
    assert artifacts_env.upload("r", uri) is True
    entry = artifacts_env.artifact
    assert artifacts_env.uploads == []
    assert entry.type == "custom"
    assert entry.uri == uri
    assert entry.content_size is None
    assert entry.hash is None
    assert entry.type_data.content_type == "text/csv"
    assert entry.type_data.preview == ""


def test_a_remote_uri_keeps_the_users_preview(artifacts_env):
    artifacts_env.upload("r", "s3://bucket/data.bin", preview="mine")
    assert artifacts_env.artifact.type_data.preview == "mine"
    assert artifacts_env.artifact.type_data.content_type == "application/octet-stream"


def test_a_short_string_is_stored_as_a_text_file(artifacts_env):
    artifacts_env.upload("s", "hello world")
    check(
        artifacts_env,
        type="string",
        uri_name="s.txt",
        content_type="text/plain",
        preview="hello world",
        ext=".txt",
        override="s.txt",
        delete=True,
        data=b"hello world",
    )
    assert artifacts_env.leftovers() == []


def test_a_string_with_a_preview_keeps_the_users_preview(artifacts_env):
    artifacts_env.upload("s", "hello world", preview="mine")
    assert artifacts_env.artifact.type_data.preview == "mine"


def test_a_long_string_gets_a_cut_preview(artifacts_env):
    limit = artifacts_env.manager.max_preview_size_bytes
    text = "t" * (limit + 5)
    artifacts_env.upload("s", text)
    assert artifacts_env.artifact.type_data.preview == (
        "# full text too large to store, storing first {}kb\n{}".format(limit // 1024, text[:limit])
    )
    assert artifacts_env.uploads[0]["data"].decode() == text


def test_a_string_that_names_no_file_is_stored_as_text(artifacts_env, tmp_path):
    artifacts_env.upload("s", str(tmp_path / "no-such-file"))
    assert artifacts_env.artifact.type == "string"


def test_a_failing_string_write_raises_and_leaves_no_temp_file(artifacts_env):
    with pytest.raises(UnicodeEncodeError):
        artifacts_env.upload("s", "bad \ud800 text")
    assert artifacts_env.uploads == []
    assert artifacts_env.leftovers() == []

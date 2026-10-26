"""The web source: files at addresses, zip archives unpacked, a revision that changes with the files."""

import zipfile

import pytest

from cairnml.datasets import DatasetError
from cairnml.datasets.sources import source_for


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "annotations.json").write_text('{"images": []}')
    with zipfile.ZipFile(root / "images.zip", "w") as archive:
        archive.writestr("train/a.jpg", b"a")
        archive.writestr("train/b.jpg", b"b")
    return root


def url(path):
    return path.as_uri()


def test_the_files_are_fetched_and_the_zip_is_unpacked(site, tmp_path):
    links = [url(site / "annotations.json"), url(site / "images.zip")]
    source = source_for("web")
    dest = tmp_path / "out"
    source.fetch(links, source.resolve(links, None), dest)

    assert (dest / "annotations.json").read_text() == '{"images": []}'
    assert (dest / "images" / "train" / "a.jpg").read_bytes() == b"a"
    assert not (dest / "images.zip").exists()


def test_the_revision_is_the_same_until_a_file_changes(site):
    links = [url(site / "annotations.json"), url(site / "images.zip")]
    source = source_for("web")
    first = source.resolve(links, None)
    assert source.resolve(links, None) == first
    (site / "annotations.json").write_text('{"images": [], "changed": true}')
    assert source.resolve(links, None) != first


def test_a_given_revision_is_used_as_it_is(site):
    assert source_for("web").resolve(url(site / "annotations.json"), "2020-release") == "2020-release"


def test_only_web_addresses_are_accepted():
    with pytest.raises(DatasetError, match="not a web address"):
        source_for("web").resolve(["/local/path.json"], None)

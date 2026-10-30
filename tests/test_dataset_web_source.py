"""The web source: files at addresses, zip archives unpacked, a revision that changes with the files."""

import gzip
import hashlib
import tarfile
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


def sha256_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def test_a_fetch_entry_with_a_matching_checksum_is_downloaded(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "labels.bin").write_bytes(b"labels")
    entries = [{"url": url(root / "labels.bin"), "checksum": sha256_of(b"labels")}]
    source = source_for("web")
    dest = tmp_path / "out"
    source.fetch(entries, source.resolve(entries, None), dest)

    assert (dest / "labels.bin").read_bytes() == b"labels"


def test_a_checksum_mismatch_refuses_the_download_and_names_the_file(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "labels.bin").write_bytes(b"tampered")
    entries = [{"url": url(root / "labels.bin"), "checksum": sha256_of(b"labels")}]
    source = source_for("web")
    with pytest.raises(DatasetError, match="labels.bin .* does not match its sha256 checksum"):
        source.fetch(entries, "any", tmp_path / "out")


def test_the_revision_of_checksummed_files_is_a_hash_of_the_checksums_without_the_network(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("the server must not be asked when every file has a checksum")

    monkeypatch.setattr("cairnml.datasets.sources.urllib.request.urlopen", no_network)
    source = source_for("web")
    entries = [{"url": "https://example.org/a.gz", "checksum": "md5:" + "a" * 32}]
    first = source.resolve(entries, None)
    assert source.resolve(entries, None) == first
    other = [{"url": "https://example.org/a.gz", "checksum": "md5:" + "b" * 32}]
    assert source.resolve(other, None) != first


def test_a_tar_gz_is_unpacked_into_a_folder_named_after_it(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    payload = tmp_path / "payload" / "train"
    payload.mkdir(parents=True)
    (payload / "a.txt").write_text("alpha")
    with tarfile.open(root / "images.tar.gz", "w:gz") as archive:
        archive.add(payload, arcname="train")
    data = (root / "images.tar.gz").read_bytes()
    entries = [{"url": url(root / "images.tar.gz"), "checksum": sha256_of(data), "unpack": "tar.gz"}]
    source = source_for("web")
    dest = tmp_path / "out"
    source.fetch(entries, source.resolve(entries, None), dest)

    assert (dest / "images" / "train" / "a.txt").read_text() == "alpha"
    assert not (dest / "images.tar.gz").exists()


def test_a_gz_file_is_decompressed_in_place_of_the_archive(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "labels.gz").write_bytes(gzip.compress(b"one file"))
    entries = [{"url": url(root / "labels.gz"), "unpack": "gz"}]
    source = source_for("web")
    dest = tmp_path / "out"
    source.fetch(entries, "any", dest)

    assert (dest / "labels").read_bytes() == b"one file"
    assert not (dest / "labels.gz").exists()


def test_a_fetch_entry_is_checked_when_it_is_read(tmp_path):
    cases = [
        ({"url": "https://example.org/a.bin", "checksum": "md5:xyz"}, "must be md5:<32 hex digits>"),
        ({"url": "https://example.org/a.bin", "checksum": "sha1:" + "a" * 40}, "must be md5:<32 hex digits>"),
        ({"url": "https://example.org/a.bin", "unpack": "rar"}, "unpack of https://example.org/a.bin must be one of"),
        ({"url": "https://example.org/a.bin", "size": 3}, "unknown key size"),
        ({"checksum": "md5:" + "a" * 32}, "not a web address"),
    ]
    for entry, message in cases:
        with pytest.raises(DatasetError, match=message):
            source_for("web").resolve([entry], "any")


def test_an_unpack_that_does_not_match_the_file_name_is_refused(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "labels.bin").write_bytes(b"x")
    entries = [{"url": url(root / "labels.bin"), "unpack": "zip"}]
    with pytest.raises(DatasetError, match="is not a .zip file"):
        source_for("web").fetch(entries, "any", tmp_path / "out")


def test_a_pin_file_describes_its_files_with_a_fetch_list(tmp_path):
    from cairnml.datasets.external import read_pin

    tree = tmp_path / "registry"
    (tree / "datasets").mkdir(parents=True)
    (tree / "datasets" / "cifar.yaml").write_text(
        "source: web\n"
        "adapter: cifar10\n"
        "fetch:\n"
        "  - url: https://example.org/cifar-10-python.tar.gz\n"
        "    checksum: md5:" + "c" * 32 + "\n"
        "    unpack: tar.gz\n"
    )
    pin = read_pin(tree, "cifar")
    assert pin.fetch == [{
        "url": "https://example.org/cifar-10-python.tar.gz",
        "checksum": "md5:" + "c" * 32,
        "unpack": "tar.gz",
    }]
    assert pin.link == ["https://example.org/cifar-10-python.tar.gz"]


def test_a_pin_file_names_either_a_link_or_a_fetch_list(tmp_path):
    from cairnml.datasets.external import read_pin

    tree = tmp_path / "registry"
    (tree / "datasets").mkdir(parents=True)
    (tree / "datasets" / "both.yaml").write_text(
        "source: web\nadapter: a\nlink: https://example.org/a.zip\n"
        "fetch:\n  - https://example.org/b.zip\n"
    )
    (tree / "datasets" / "hub.yaml").write_text(
        "source: huggingface\nadapter: a\nlink: hf://datasets/org/a\n"
        "fetch:\n  - https://example.org/b.zip\n"
    )
    with pytest.raises(DatasetError, match="sets both link and fetch"):
        read_pin(tree, "both")
    with pytest.raises(DatasetError, match="fetch is only for a source of web"):
        read_pin(tree, "hub")

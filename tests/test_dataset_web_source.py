"""A pin file's ``fetch`` list is passed to the adapter as it is: the SDK reads the pin, the adapter downloads the files."""

import pytest

from cairnml.datasets import DatasetCache, DatasetError
from cairnml.datasets.external import Pin, _ensure_source, read_pin


def write_pin(tmp_path, text):
    tree = tmp_path / "registry"
    (tree / "datasets").mkdir(parents=True, exist_ok=True)
    (tree / "datasets" / "web.yaml").write_text(text)
    return tree


def test_a_pin_file_passes_its_fetch_list_on_as_written(tmp_path):
    tree = write_pin(tmp_path, (
        "source: web\n"
        "adapter: cifar10\n"
        "fetch:\n"
        "  - url: https://example.org/cifar-10-python.tar.gz\n"
        "    checksum: md5:" + "c" * 32 + "\n"
        "    unpack: tar.gz\n"
    ))
    pin = read_pin(tree, "web")
    assert pin.link == [{
        "url": "https://example.org/cifar-10-python.tar.gz",
        "checksum": "md5:" + "c" * 32,
        "unpack": "tar.gz",
    }]


def test_a_pin_file_names_either_a_link_or_a_fetch_list(tmp_path):
    tree = write_pin(tmp_path, "source: web\nadapter: a\nlink: https://example.org/a.zip\nfetch:\n  - https://example.org/b.zip\n")
    with pytest.raises(DatasetError, match="sets both link and fetch"):
        read_pin(tree, "web")

    tree = write_pin(tmp_path, "source: web\nadapter: a\n")
    with pytest.raises(DatasetError, match="must set 'link'"):
        read_pin(tree, "web")


def test_the_fetch_of_the_adapter_receives_the_list_and_the_revision(tmp_path):
    calls = []

    def fetch(link, revision, dest):
        calls.append((link, revision, dest))
        dest.mkdir(parents=True)
        (dest / "done").write_text("yes")

    entries = [{"url": "https://example.org/a.gz", "checksum": "md5:" + "a" * 32, "unpack": "gz"}]
    pin = Pin(name="web", source="web", adapter="a", link=entries)
    cache = DatasetCache(tmp_path / "cache", max_bytes=10**9)
    folder = _ensure_source(cache, pin, fetch, entries, "rev-1")

    assert calls == [(entries, "rev-1", folder)]
    assert (folder / "done").read_text() == "yes"

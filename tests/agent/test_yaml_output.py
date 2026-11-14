"""The YAML text the agent writes and reads stays the same whichever YAML library provides it."""

from collections import OrderedDict

from cairnml_agent.helper import base


def test_a_mapping_is_dumped_with_sorted_keys_and_flow_lists():
    text = base.dump_yaml({"zeta": 1, "alpha": {"nested_b": [1, 2], "nested_a": "x"}, "mid": None})
    assert text == "alpha:\n  nested_a: x\n  nested_b: [1, 2]\nmid: null\nzeta: 1\n"


def test_an_ordered_mapping_is_dumped_as_an_omap_when_dumping_all_types():
    text = base.dump_yaml(OrderedDict([("b", 1), ("a", 2)]), dump_all=True)
    assert text == "!!omap [b: 1, a: 2]\n"


def test_a_yaml_file_round_trips_through_load_and_dump(tmp_path):
    path = tmp_path / "x.yaml"
    base.dump_yaml({"a": [1, "two"], "b": {"c": True}}, path=path)
    assert base.load_yaml(path) == {"a": [1, "two"], "b": {"c": True}}
    assert path.read_text() == "a: [1, two]\nb: {c: true}\n"


def test_a_malformed_yaml_file_raises_value_error(tmp_path):
    import pytest

    path = tmp_path / "bad.yaml"
    path.write_text("a: [1, 2\n")
    with pytest.raises(ValueError, match="Failed parsing yaml file"):
        base.load_yaml(path)

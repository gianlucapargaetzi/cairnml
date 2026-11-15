"""The configuration text the SDK writes and reads back is pinned: it is stored on the server and read by other tools."""

from cairnml.utilities.config import config_dict_to_text, text_to_config_dict

SAMPLE = {
    "general": {"base_task_id": "abc", "random_seed": 42, "use_gpu": True, "ratio": 0.5},
    "names": ["a", "b"],
    "empty": None,
    "text": 'quoted "value"',
}

PINNED_TEXT = (
    'general {\n'
    '  base_task_id = "abc"\n'
    '  random_seed = 42\n'
    '  use_gpu = true\n'
    '  ratio = 0.5\n'
    '}\n'
    'names = [\n'
    '  "a"\n'
    '  "b"\n'
    ']\n'
    'empty = null\n'
    'text = "quoted \\"value\\""'
)


def test_a_dict_is_written_as_pinned_hocon_text():
    assert config_dict_to_text(SAMPLE) == PINNED_TEXT


def test_the_pinned_text_reads_back_as_the_same_dict():
    assert text_to_config_dict(PINNED_TEXT) == SAMPLE


def test_a_string_is_kept_as_it_is():
    text = "some: 1\n"
    assert config_dict_to_text(text) == text

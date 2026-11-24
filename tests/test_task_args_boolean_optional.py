"""Task parameters of argparse options are applied to the parser the way a remote task does it (cairnml#193).

A ``BooleanOptionalAction`` option (``--flag/--no-flag``) takes no value on the command line, so argparse gives it
``nargs=0``. Its stored task parameter is the string ``"True"`` or ``"False"`` and has to arrive as a ``bool``.
"""

import argparse
import logging

import pytest

from cairnml.backend_interface.task.args import _Arguments


class FakeTask:
    """Just enough of a task for ``_Arguments.copy_to_parser``: its stored parameters and a logger."""

    def __init__(self, params):
        self._params = params
        self.log = logging.getLogger("fake_task")

    def get_parameters(self):
        return self._params

    def set_parameter(self, *args, **kwargs):
        pass


def apply_stored(parser, stored, dest="opt"):
    """Apply the stored value of option ``dest`` as a remote task does.

    Returns the value in the namespace of the parsed arguments and the value ``parse_args([])`` gives afterwards.
    """
    namespace = parser.parse_args([])
    _Arguments(FakeTask({"Args/" + dest: stored})).copy_to_parser(parser, namespace)
    return getattr(namespace, dest), getattr(parser.parse_args([]), dest)


def boolean_optional_parser(default):
    parser = argparse.ArgumentParser()
    parser.add_argument("--opt", action=argparse.BooleanOptionalAction, default=default)
    return parser


# What the options other than BooleanOptionalAction gave before cairnml#193, recorded from the unchanged code.
UNCHANGED = [
    pytest.param(dict(nargs="+", type=int, default=[5]), "[1, 2, 3]", [1, 2, 3], id="nargs-plus-list"),
    pytest.param(dict(nargs="+", type=int, default=[5]), "1", [1], id="nargs-plus-single"),
    pytest.param(dict(nargs="*", default=["a"]), "['x', 'y']", ["x", "y"], id="nargs-star"),
    pytest.param(dict(nargs=2, type=float, default=[0.5, 1.5]), "[1, 2]", [1.0, 2.0], id="nargs-two"),
    pytest.param(dict(action="append", default=["a"]), "['b', 'c']", ["b", "c"], id="append"),
    pytest.param(dict(action="store_true"), "True", True, id="store-true"),
    pytest.param(dict(type=int, default=3), "7", 7, id="int-option"),
]


@pytest.mark.parametrize("kwargs, stored, expected", UNCHANGED)
def test_unchanged_other_options_keep_their_values(kwargs, stored, expected):
    parser = argparse.ArgumentParser()
    parser.add_argument("--opt", **kwargs)
    assert apply_stored(parser, stored) == (expected, expected)


BOOLEAN_WORDS = [
    ("True", True),
    ("False", False),
    ("true", True),
    ("false", False),
    ("1", True),
    ("0", False),
]


@pytest.mark.parametrize("default", [False, True, None])
@pytest.mark.parametrize("stored, expected", BOOLEAN_WORDS)
def test_boolean_optional_values_arrive_as_booleans(default, stored, expected):
    in_namespace, parsed = apply_stored(boolean_optional_parser(default), stored)
    assert in_namespace is expected
    assert parsed is expected


@pytest.mark.parametrize("default", [False, True, None])
def test_boolean_optional_values_that_are_not_booleans_keep_the_default_and_warn(default, caplog):
    caplog.set_level(logging.WARNING)
    in_namespace, parsed = apply_stored(boolean_optional_parser(default), "maybe")
    assert in_namespace is default
    assert parsed is default
    assert 'Failed parsing task parameter opt="maybe"' in caplog.text


@pytest.mark.parametrize("default", [False, True, None])
def test_boolean_optional_values_empty_means_not_set_and_keeps_the_default(default, caplog):
    caplog.set_level(logging.WARNING)
    in_namespace, parsed = apply_stored(boolean_optional_parser(default), "")
    assert in_namespace is default
    assert parsed is default
    assert "Failed parsing" not in caplog.text


@pytest.mark.parametrize("default", [False, True, None])
@pytest.mark.parametrize("stored", [word for word, _ in BOOLEAN_WORDS])
def test_boolean_optional_logs_no_warning_for_a_boolean(default, stored, caplog):
    caplog.set_level(logging.WARNING)
    apply_stored(boolean_optional_parser(default), stored)
    assert "Failed parsing" not in caplog.text

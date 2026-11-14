"""The JSON models of the session package: the scalar types they compare, and the validators that import the same module."""

import importlib

import pytest

PACKAGE = "cairnml_agent.backend_api.session.jsonmodels"

# The modules are imported inside each test, not at the top: with a top-level import a broken module would be a collection error
# that hides every test of the file, and here each test must fail on its own.


def test_the_scalar_types_of_the_json_models():
    utilities = importlib.import_module(PACKAGE + ".utilities")

    assert utilities.SCALAR_TYPES == (str, int, float, bool)


def test_the_validators_import_and_work():
    validators = importlib.import_module(PACKAGE + ".validators")
    errors = importlib.import_module(PACKAGE + ".errors")

    validators.Min(3).validate(5)
    with pytest.raises(errors.ValidationError):
        validators.Min(3).validate(1)


def test_a_schema_is_compared_by_value_for_every_scalar_type():
    utilities = importlib.import_module(PACKAGE + ".utilities")

    for value, other in (("a", "b"), (1, 2), (1.5, 2.5), (True, False)):
        assert isinstance(value, utilities.SCALAR_TYPES)  # the comparison below is the one for scalars
        assert utilities.compare_schemas(value, value) is True
        assert utilities.compare_schemas(value, other) is False
    # lists of a schema are compared without their order
    assert utilities.compare_schemas({"k": [1, 2]}, {"k": [2, 1]}) is True

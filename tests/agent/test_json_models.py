"""The JSON models of the session package: the scalar types they compare, and the validators that import the same module."""

import importlib

import pytest

PACKAGE = "jsonmodels"

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


def test_the_session_response_meta_is_validated_from_the_package():
    response = importlib.import_module("cairnml.backend_api.session.response")

    meta = response.ResponseMeta(
        id="a",
        trx="b",
        endpoint=response._ResponseEndpoint(name="x"),
        result_code=200,
        result_msg="ok",
        result_subcode=1,
    )
    meta.validate()
    assert (meta.id, meta.endpoint.name, meta.result_code, meta.result_subcode) == ("a", "x", 200, 1)

    with pytest.raises(importlib.import_module("jsonmodels.errors").ValidationError):
        response.ResponseMeta(
            id="a", trx="b", endpoint=response._ResponseEndpoint(name="x"), result_msg="ok"
        ).validate()

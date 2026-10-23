"""Smoke tests for the CairnML SDK at the port baseline.

The upstream SDK repository has no tests at this tag, so these cover only what the port
depends on: the package imports and exposes a version, and the configuration loads.
Session and device-key tests are added with the SDK changes in Phase 2 of the plan.
"""


def test_package_imports_and_reports_a_version():
    import cairnml

    assert isinstance(cairnml.__version__, str)
    assert cairnml.__version__


def test_config_loads_without_a_server():
    from cairnml.config import config_obj

    assert config_obj is not None

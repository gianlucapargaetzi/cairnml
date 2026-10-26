"""Temporary S3 keys go into dvc's local config intact, including a secret that starts with a dash."""

import pytest

from cairnml.datasets.dvcutil import Dvc


def test_credentials_with_a_leading_dash_are_stored_unchanged(tmp_path):
    dvc = Dvc(tmp_path)
    dvc.init(no_scm=True)
    dvc.set_remote("s3://bucket/prefix")
    dash_secret = "-" + "0" * 40
    dvc.set_credentials("access", dash_secret, "token")
    assert dvc.run("config", "--local", "remote.storage.secret_access_key") == dash_secret
    assert dvc.run("config", "--local", "remote.storage.session_token") == "token"

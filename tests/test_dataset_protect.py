"""``cairnml dataset create`` protects the default branch through gh. The gh call is mocked by a fake gh on PATH."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from cairnml.cli.dataset import __main__ as dataset_cli
from cairnml.datasets import DatasetRepo, SampleWriter
from cairnml.datasets import repo as repo_module
from cairnml.datasets.repo import protection_body, protection_problems

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "lfs", "version"], capture_output=True).returncode != 0, reason="git-lfs is not installed"
)

FAKE_GH = """#!{python}
import json, os, sys
args = sys.argv[1:]
body = sys.stdin.read() if "--input" in args else ""
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps({{"args": args, "body": body}}) + "\\n")
if args[:1] == ["auth"]:
    sys.exit(0)
if args[:1] == ["api"] and "check-runs" in args[1]:
    print(os.environ.get("FAKE_GH_CHECKS", '{{"check_runs": []}}'))
    sys.exit(0)
if args[:1] == ["api"] and "-X" in args:
    sys.stderr.write(os.environ.get("FAKE_GH_PUT_ERR", ""))
    sys.exit(int(os.environ.get("FAKE_GH_PUT_CODE", "0")))
sys.exit(2)
"""


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for name, value in (("NAME", "Tester"), ("EMAIL", "tester@example.com")):
        monkeypatch.setenv(f"GIT_AUTHOR_{name}", value)
        monkeypatch.setenv(f"GIT_COMMITTER_{name}", value)


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    """A gh that logs each call to a file and answers the check-runs read and the protection write."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "gh"
    script.write_text(FAKE_GH.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "gh.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    monkeypatch.setattr(repo_module, "CHECK_WAIT_SEC", 0.0)
    monkeypatch.setattr(repo_module, "CHECK_POLL_SEC", 0.0)

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return calls


@pytest.fixture
def origin(tmp_path):
    path = tmp_path / "server" / "acme" / "cars.git"  # the owner and name are read from this path
    subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True)
    return path


@pytest.fixture
def source(tmp_path):
    prepared = tmp_path / "prepared"
    (prepared / "data").mkdir(parents=True)
    (prepared / "data" / "one.txt").write_bytes(b"one")
    data = prepared / "data"
    writer = SampleWriter(data, {"file": "file"}, {}, source_root=data)
    writer.add("one.txt", "train", {"file": "one.txt"}, {})
    writer.close()
    return data


def create(tmp_path, origin, source, **kwargs):
    return DatasetRepo.create(
        tmp_path / "local", "cars", source, storage="lfs", create_remote=lambda name, private: str(origin), **kwargs
    )


def protection_calls(calls):
    return [c for c in calls() if "-X" in c["args"]]


def test_create_protects_main_and_requires_the_check_once_it_has_run(tmp_path, origin, source, fake_gh, monkeypatch):
    monkeypatch.setenv("FAKE_GH_CHECKS", json.dumps({"check_runs": [{"name": "verify"}]}))
    result = create(tmp_path, origin, source)
    assert result["protection"] == {"ok": True, "check": "verify", "problem": None}
    (put,) = protection_calls(fake_gh)
    assert put["args"][:4] == ["api", "-X", "PUT", "repos/acme/cars/branches/main/protection"]
    body = json.loads(put["body"])
    assert body["required_status_checks"] == {"strict": False, "contexts": ["verify"]}
    assert body["required_pull_request_reviews"]["required_approving_review_count"] == 0
    assert body["enforce_admins"] is False
    assert body["allow_force_pushes"] is False and body["allow_deletions"] is False
    assert body["restrictions"] is None
    assert protection_problems({
        "required_pull_request_reviews": {}, "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False}, "required_status_checks": {"contexts": ["verify"]},
    }) == []


def test_without_a_check_run_the_branch_is_protected_with_no_required_check(tmp_path, origin, source, fake_gh):
    result = create(tmp_path, origin, source)
    assert result["protection"] == {"ok": True, "check": None, "problem": None}
    (put,) = protection_calls(fake_gh)
    assert json.loads(put["body"])["required_status_checks"] is None
    assert protection_body(None)["required_status_checks"] is None


def test_a_403_is_a_warning_that_names_the_permission_and_does_not_fail_the_create(tmp_path, origin, source, fake_gh, monkeypatch):
    monkeypatch.setenv("FAKE_GH_PUT_CODE", "1")
    monkeypatch.setenv("FAKE_GH_PUT_ERR", "gh: Must have admin rights to Repository. (HTTP 403)")
    result = create(tmp_path, origin, source)
    assert result["commit"] and result["branch"] == "main"  # the dataset is published anyway
    assert result["protection"]["ok"] is False and result["protection"]["check"] is None
    assert "administration rights" in result["protection"]["problem"] and "403" in result["protection"]["problem"]
    assert (tmp_path / "local" / "data" / "one.txt").exists()


def test_no_protect_skips_the_call_and_the_branch_stays_open(tmp_path, origin, source, fake_gh):
    result = create(tmp_path, origin, source, protect=False)
    assert result["protection"] is None
    assert protection_calls(fake_gh) == []


def test_the_cli_passes_no_protect_on(monkeypatch, tmp_path):
    seen = {}

    def fake_create(path, name, source, **kwargs):
        seen.update(kwargs)
        return {"url": "u", "where": "w", "commit": "c" * 40, "branch": "main", "protection": None}

    monkeypatch.setattr(dataset_cli.DatasetRepo, "create", staticmethod(fake_create))
    dataset_cli.cli(["create", "cars", "--from", str(tmp_path), "--no-protect"])
    assert seen["protect"] is False
    dataset_cli.cli(["create", "cars", "--from", str(tmp_path)])
    assert seen["protect"] is True

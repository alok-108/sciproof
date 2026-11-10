"""Tests for the SciProof pytest plugin."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from sciproof.pytest_plugin import (
    SciProofFile,
    pytest_addoption,
    pytest_collect_file,
)


def test_pytest_plugin_collection_and_execution(project):
    project.capture(name="manifest1")
    manifest_path = project.root / "manifest1.sciproof.json"

    # Test via subprocess running pytest with the plugin enabled
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "-p",
        "sciproof.pytest_plugin",
        "-p",
        "no:sciproof",
        "--sciproof",
        str(manifest_path),
    ]
    res = subprocess.run(cmd, cwd=project.root, capture_output=True, text=True, check=False)
    assert res.returncode == 0
    assert "1 passed" in res.stdout


def test_pytest_plugin_failure_reporting(project):
    project.capture(name="manifest2")
    (project.root / "out" / "exact.txt").write_text("tampered")
    manifest_path = project.root / "manifest2.sciproof.json"

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "-p",
        "sciproof.pytest_plugin",
        "-p",
        "no:sciproof",
        "--sciproof",
        str(manifest_path),
    ]
    res = subprocess.run(cmd, cwd=project.root, capture_output=True, text=True, check=False)
    assert res.returncode != 0
    assert "SciProof verification FAILED" in res.stdout or "1 failed" in res.stdout


def test_pytest_addoption():
    parser = MagicMock()
    pytest_addoption(parser)
    assert parser.getgroup.called


def test_pytest_collect_file_ignored_when_flag_not_set(tmp_path):
    parent = MagicMock()
    parent.config.getoption.return_value = False
    assert pytest_collect_file(tmp_path / "exp.sciproof.json", parent) is None
    parent.config.getoption.return_value = True
    assert pytest_collect_file(tmp_path / "exp.txt", parent) is None


def test_pytest_item_direct_run(project):
    project.capture(name="item_test")
    manifest = project.root / "item_test.sciproof.json"

    session = MagicMock()
    session.config.getoption.return_value = False
    file_item = SciProofFile.from_parent(session, path=manifest)
    items = file_item.collect()
    assert len(items) == 1
    item = items[0]
    path, line, desc = item.reportinfo()
    assert desc == "sciproof:item_test.sciproof.json"
    item.runtest()  # should pass without raising


def test_pytest_item_direct_run_inconclusive(project, monkeypatch):
    from sciproof.verify import Check, Report, Status

    project.capture(name="inconcl_test")
    manifest = project.root / "inconcl_test.sciproof.json"

    def mock_verify(*args, **kwargs):
        return Report(
            name="test",
            rerun_requested=False,
            checks=[Check(Status.INCONCLUSIVE, "check", "inconclusive message")],
        )

    monkeypatch.setattr("sciproof.pytest_plugin.verify", mock_verify)

    session = MagicMock()
    session.config.getoption.return_value = False
    file_item = SciProofFile.from_parent(session, path=manifest)
    item = file_item.collect()[0]
    with pytest.raises(pytest.skip.Exception):
        item.runtest()


def test_pytest_item_direct_run_fail(project, monkeypatch):
    from sciproof.verify import Check, Report, Status

    project.capture(name="fail_test")
    manifest = project.root / "fail_test.sciproof.json"

    def mock_verify(*args, **kwargs):
        return Report(
            name="test",
            rerun_requested=False,
            checks=[Check(Status.FAIL, "check", "fail message")],
        )

    monkeypatch.setattr("sciproof.pytest_plugin.verify", mock_verify)

    session = MagicMock()
    session.config.getoption.return_value = False
    file_item = SciProofFile.from_parent(session, path=manifest)
    item = file_item.collect()[0]
    with pytest.raises(pytest.fail.Exception):
        item.runtest()


def test_pytest_item_direct_run_corrupt_manifest(project):
    bad_manifest = project.root / "corrupt.sciproof.json"
    bad_manifest.write_text("{ corrupt json", encoding="utf-8")

    session = MagicMock()
    session.config.getoption.return_value = False
    file_item = SciProofFile.from_parent(session, path=bad_manifest)
    item = file_item.collect()[0]
    with pytest.raises(pytest.fail.Exception) as exc_info:
        item.runtest()
    assert "SciProof manifest verification error" in str(exc_info.value)


def test_pytest_plugin_corrupt_manifest_reported_as_failure(project):
    bad_manifest = project.root / "corrupt.sciproof.json"
    bad_manifest.write_text("{ corrupt json", encoding="utf-8")

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "-p",
        "sciproof.pytest_plugin",
        "-p",
        "no:sciproof",
        "--sciproof",
        str(bad_manifest),
    ]
    res = subprocess.run(cmd, cwd=project.root, capture_output=True, text=True, check=False)
    assert res.returncode != 0
    assert "1 failed" in res.stdout
    assert "1 error" not in res.stdout
    assert "SciProof manifest verification error" in res.stdout

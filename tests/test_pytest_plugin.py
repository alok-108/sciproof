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


def test_pytest_plugin_options_passed_to_verify(tmp_path, monkeypatch):
    from pathlib import Path

    from sciproof.verify import Check, Report, Status

    manifest = tmp_path / "exp.sciproof.json"
    manifest.write_text('{"name": "exp"}', encoding="utf-8")

    called_kwargs = {}

    def mock_verify(path, **kwargs):
        called_kwargs.update(kwargs)
        return Report(
            name="exp",
            rerun_requested=kwargs.get("rerun", False),
            checks=[Check(Status.PASS, "manifest", "ok")],
        )

    monkeypatch.setattr("sciproof.pytest_plugin.verify", mock_verify)

    config_opts = {
        "--sciproof": True,
        "--sciproof-rerun": True,
        "--sciproof-clean-env": True,
        "--sciproof-pass-env": ["FOO", "BAR"],
        "--sciproof-timeout": 42.0,
        "--sciproof-policy": Path("policy.toml"),
        "--sciproof-allowed-signers": Path("signers.txt"),
    }

    session = MagicMock()
    session.config.getoption.side_effect = lambda name, default=None: config_opts.get(name, default)
    session.config.getini.return_value = ""

    file_item = SciProofFile.from_parent(session, path=manifest)
    item = file_item.collect()[0]
    item.runtest()

    assert called_kwargs["rerun"] is True
    assert called_kwargs["clean_env"] is True
    assert called_kwargs["pass_env"] == ["FOO", "BAR"]
    assert called_kwargs["timeout"] == 42.0
    assert called_kwargs["policy"] == Path("policy.toml")
    assert called_kwargs["allowed_signers"] == Path("signers.txt")


def test_pytest_plugin_ini_options_passed_to_verify(tmp_path, monkeypatch):
    from sciproof.verify import Check, Report, Status

    manifest = tmp_path / "exp.sciproof.json"
    manifest.write_text('{"name": "exp"}', encoding="utf-8")

    called_kwargs = {}

    def mock_verify(path, **kwargs):
        called_kwargs.update(kwargs)
        return Report(
            name="exp",
            rerun_requested=kwargs.get("rerun", False),
            checks=[Check(Status.PASS, "manifest", "ok")],
        )

    monkeypatch.setattr("sciproof.pytest_plugin.verify", mock_verify)

    ini_opts = {
        "sciproof": True,
        "sciproof_rerun": True,
        "sciproof_clean_env": True,
        "sciproof_pass_env": ["MY_VAR"],
        "sciproof_timeout": "15.0",
        "sciproof_policy": "my_policy.toml",
        "sciproof_allowed_signers": "my_signers.txt",
    }

    session = MagicMock()
    session.config.getoption.side_effect = lambda name, default=None: default
    session.config.getini.side_effect = lambda name: ini_opts.get(name, "")

    file_item = SciProofFile.from_parent(session, path=manifest)
    item = file_item.collect()[0]
    item.runtest()

    assert called_kwargs["rerun"] is True
    assert called_kwargs["clean_env"] is True
    assert called_kwargs["pass_env"] == ["MY_VAR"]
    assert called_kwargs["timeout"] == 15.0
    assert called_kwargs["policy"] == "my_policy.toml"
    assert called_kwargs["allowed_signers"] == "my_signers.txt"


def test_pytest_plugin_clean_env_subprocess(project, monkeypatch):
    script = (
        "import os, pathlib\n"
        "val = os.environ.get('SECRET_TOKEN', '')\n"
        "pathlib.Path('out').mkdir(exist_ok=True)\n"
        "pathlib.Path('out/token.txt').write_text(val)\n"
    )
    (project.root / "token_app.py").write_text(script)
    monkeypatch.delenv("SECRET_TOKEN", raising=False)
    project.capture(
        name="token_manifest",
        argv=[sys.executable, "token_app.py"],
        inputs=["token_app.py"],
        outputs=["out/token.txt"],
        numeric_outputs=[],
    )
    manifest_path = project.root / "token_manifest.sciproof.json"

    # Run with SECRET_TOKEN in env and --sciproof-clean-env:
    # Minimal env strips SECRET_TOKEN, so token.txt remains empty, matching baseline
    cmd_clean = [
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
        "--sciproof-rerun",
        "--sciproof-clean-env",
        str(manifest_path),
    ]
    env_with_secret = {"SECRET_TOKEN": "supersecret"}
    res_clean = subprocess.run(
        cmd_clean,
        cwd=project.root,
        capture_output=True,
        text=True,
        check=False,
        env={**dict(subprocess.os.environ), **env_with_secret},
    )
    assert res_clean.returncode == 0
    assert "1 passed" in res_clean.stdout

    # Now pass --sciproof-pass-env SECRET_TOKEN:
    # SECRET_TOKEN is passed, writing 'supersecret' which differs from baseline (empty) -> fails
    cmd_pass = [
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
        "--sciproof-rerun",
        "--sciproof-clean-env",
        "--sciproof-pass-env",
        "SECRET_TOKEN",
        str(manifest_path),
    ]
    res_pass = subprocess.run(
        cmd_pass,
        cwd=project.root,
        capture_output=True,
        text=True,
        check=False,
        env={**dict(subprocess.os.environ), **env_with_secret},
    )
    assert res_pass.returncode != 0
    assert "1 failed" in res_pass.stdout

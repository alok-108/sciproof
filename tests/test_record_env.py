import json
from pathlib import Path

import pytest

from sciproof.capture import capture
from sciproof.diffmanifests import diff_manifests
from sciproof.environment import recorded_env_vars
from sciproof.errors import UsageError


def test_recorded_env_values_and_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("SP_TEST_VAR", "hello")
    monkeypatch.delenv("SP_ABSENT_VAR", raising=False)
    (tmp_path / "run.py").write_text("open('out.txt', 'w').write('x')\n")
    result = capture(
        name="t",
        argv=["python3", "run.py"],
        base=tmp_path,
        inputs=["run.py"],
        outputs=["out.txt"],
        record_env=["SP_TEST_VAR", "SP_ABSENT_VAR"],
    )
    manifest = json.loads(Path(result.manifest_path).read_text())
    assert manifest["schema_version"] == "1.1"
    assert manifest["environment"]["recorded_env"] == {
        "SP_TEST_VAR": "hello",
        "SP_ABSENT_VAR": None,
    }


def test_recorded_env_refuses_secret_like_names():
    with pytest.raises(UsageError):
        recorded_env_vars(["MY_API_KEY"])
    with pytest.raises(UsageError):
        recorded_env_vars(["MY_TOKEN"])


def test_diff_reports_recorded_env_change(tmp_path, monkeypatch):
    (tmp_path / "run.py").write_text("open('out.txt', 'w').write('x')\n")
    monkeypatch.setenv("SP_TEST_VAR", "one")
    a = capture(
        name="a",
        argv=["python3", "run.py"],
        base=tmp_path,
        inputs=["run.py"],
        outputs=["out.txt"],
        record_env=["SP_TEST_VAR"],
        force=True,
    )
    monkeypatch.setenv("SP_TEST_VAR", "two")
    b = capture(
        name="b",
        argv=["python3", "run.py"],
        base=tmp_path,
        inputs=["run.py"],
        outputs=["out.txt"],
        record_env=["SP_TEST_VAR"],
        force=True,
    )
    report = diff_manifests(a.manifest_path, b.manifest_path)
    assert any(
        c.subject == "recorded variable SP_TEST_VAR" and c.kind == "changed" for c in report.changes
    )

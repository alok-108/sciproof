"""Multi-step pipeline DAG tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sciproof.capture import capture
from sciproof.cli import main
from sciproof.errors import UsageError
from sciproof.pipeline import load_pipeline, verify_pipeline
from sciproof.verify import Status


def test_pipeline_dag_success_and_chain_of_custody(tmp_path: Path):
    data_file = tmp_path / "data.csv"
    data_file.write_text("x,y\n1,2\n3,4\n", encoding="utf-8")

    # Step 1: data prep produces features.csv
    features_file = tmp_path / "features.csv"
    features_file.write_text("f1,f2\n1.0,2.0\n", encoding="utf-8")
    prep_res = capture(
        name="step1_prep",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        inputs=["data.csv"],
        outputs=["features.csv"],
    )

    # Step 2: train takes features.csv and produces metrics.json
    metrics_file = tmp_path / "metrics.json"
    metrics_file.write_text(json.dumps({"accuracy": 0.98}), encoding="utf-8")
    train_res = capture(
        name="step2_train",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        inputs=["features.csv"],
        outputs=["metrics.json"],
    )

    pipeline_toml = tmp_path / "pipeline.toml"
    pipeline_toml.write_text(
        f"""[pipeline]
name = "ml_workflow"

[[steps]]
name = "prep"
manifest = "{prep_res.manifest_path.name}"

[[steps]]
name = "train"
manifest = "{train_res.manifest_path.name}"
depends_on = ["prep"]
""",
        encoding="utf-8",
    )

    report = verify_pipeline(pipeline_toml)
    assert report.status is Status.PASS
    assert len(report.steps) == 2
    assert "Overall Verdict: PASS" in report.to_text()


def test_pipeline_chain_of_custody_failure(tmp_path: Path):
    # Step 1 output
    features_file = tmp_path / "features.csv"
    features_file.write_text("original", encoding="utf-8")
    prep_res = capture(
        name="step1",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        outputs=["features.csv"],
    )

    # Tamper with features before Step 2
    features_file.write_text("tampered", encoding="utf-8")
    train_res = capture(
        name="step2",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        inputs=["features.csv"],
        outputs=[],
    )

    # Restore file so step integrity check alone might pass for step 2,
    # but the recorded input sha for step 2 differs from step 1's output
    pipeline_toml = tmp_path / "pipeline.toml"
    pipeline_toml.write_text(
        f"""[pipeline]
name = "broken_workflow"

[[steps]]
name = "s1"
manifest = "{prep_res.manifest_path.name}"

[[steps]]
name = "s2"
manifest = "{train_res.manifest_path.name}"
depends_on = ["s1"]
""",
        encoding="utf-8",
    )

    report = verify_pipeline(pipeline_toml)
    assert report.status is Status.FAIL
    assert any("chain-of-custody break" in report.to_text() for s in report.steps)


def test_pipeline_cli_verify(tmp_path: Path, capsys):
    out_file = tmp_path / "res.txt"
    out_file.write_text("done", encoding="utf-8")
    res = capture(
        name="step_cli",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        outputs=["res.txt"],
    )
    pipeline_toml = tmp_path / "pipeline.toml"
    pipeline_toml.write_text(
        f"""[pipeline]
name = "cli_pipe"

[[steps]]
name = "step1"
manifest = "{res.manifest_path.name}"
""",
        encoding="utf-8",
    )

    code = main(["pipeline", "verify", str(pipeline_toml)])
    assert code == 0
    captured = capsys.readouterr()
    assert "SciProof Pipeline: cli_pipe" in captured.out
    assert "Overall Verdict: PASS" in captured.out


def test_pipeline_missing_file_raises(tmp_path: Path):
    with pytest.raises(UsageError, match="pipeline file not found"):
        load_pipeline(tmp_path / "missing.toml")

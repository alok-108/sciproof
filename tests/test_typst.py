"""Typst export: sciproof export --format typst."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sciproof.capture import capture
from sciproof.cli import main
from sciproof.errors import UsageError
from sciproof.typst import format_typst_value, to_typst


def test_format_typst_literals():
    assert format_typst_value(None) == "none"
    assert format_typst_value(True) == "true"
    assert format_typst_value(False) == "false"
    assert format_typst_value(42) == "42"
    assert format_typst_value(0.93) == "0.93"
    assert format_typst_value(float("nan")) == "calc.nan"
    assert format_typst_value(float("inf")) == "calc.inf"
    assert format_typst_value(float("-inf")) == "-calc.inf"
    assert format_typst_value("hello world") == '"hello world"'
    assert format_typst_value('with "quote"') == '"with \\"quote\\""'


def test_typst_export_auto_and_values(tmp_path: Path):
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(
        json.dumps({"accuracy": 0.952, "loss": 0.041, "model": "resnet18"}), encoding="utf-8"
    )

    result = capture(
        name="test_typst_exp",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        outputs=["metrics.json"],
    )

    # Manual values
    output = to_typst(
        result.manifest_path,
        [
            ("acc", "metrics.json", "$.accuracy"),
            ("loss_val", "metrics.json", "$.loss"),
        ],
    )
    assert "#let acc = 0.952" in output
    assert "#let loss_val = 0.041" in output
    assert "// SciProof" in output

    # Auto values
    auto_output = to_typst(result.manifest_path, None)
    assert "#let metrics_accuracy = 0.952" in auto_output or "#let accuracy = 0.952" in auto_output
    assert "#let metrics_loss = 0.041" in auto_output or "#let loss = 0.041" in auto_output


def test_typst_cli_export(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(json.dumps({"score": 99}), encoding="utf-8")

    result = capture(
        name="cli_typst_exp",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        outputs=["metrics.json"],
    )

    code = main(["export", str(result.manifest_path), "--format", "typst"])
    assert code == 0
    captured = capsys.readouterr()
    assert "#let" in captured.out
    assert "99" in captured.out


def test_typst_empty_values_raises(tmp_path: Path):
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(json.dumps({"score": 1}), encoding="utf-8")
    result = capture(
        name="cli_empty_val",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        outputs=["metrics.json"],
    )
    with pytest.raises(UsageError, match="give at least one --value"):
        to_typst(result.manifest_path, [])

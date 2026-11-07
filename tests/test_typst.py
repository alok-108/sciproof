"""Typst export: sciproof export --format typst."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sciproof.capture import capture
from sciproof.cli import main
from sciproof.errors import UsageError
from sciproof.typst import TYPST_IDENTIFIER, _auto_typst_name, format_typst_value, to_typst


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


def test_typst_reserved_keywords_rejected(tmp_path: Path):
    (tmp_path / "metrics.json").write_text('{"score": 1}', encoding="utf-8")
    result = capture(
        name="reserved_typst_names",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        outputs=["metrics.json"],
    )

    for keyword in (
        "and",
        "as",
        "auto",
        "break",
        "continue",
        "else",
        "false",
        "for",
        "if",
        "import",
        "in",
        "include",
        "let",
        "none",
        "not",
        "or",
        "return",
        "set",
        "show",
        "true",
        "while",
    ):
        with pytest.raises(UsageError, match="reserved Typst keyword"):
            to_typst(result.manifest_path, [(keyword, "metrics.json", "$.score")])

    # Typst keyword matching is case-sensitive.
    assert "#let Let = 1" in to_typst(result.manifest_path, [("Let", "metrics.json", "$.score")])


def test_typst_auto_names_avoid_reserved_keywords_and_collisions(tmp_path: Path):
    (tmp_path / "metrics.json").write_text(
        json.dumps({"let": 1, "let_val": 2, "return": 3}), encoding="utf-8"
    )
    result = capture(
        name="auto_typst_names",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        outputs=["metrics.json"],
    )

    output = to_typst(result.manifest_path)
    assert "#let let_val = 1" in output
    assert "#let let_val_1 = 2" in output
    assert "#let return_val = 3" in output
    assert "#let let =" not in output
    assert "#let return =" not in output


GENERATED = settings(max_examples=120, deadline=None, derandomize=True)


@GENERATED
@given(location=st.text(max_size=120), output_path=st.text(max_size=120))
def test_auto_typst_name_is_always_a_valid_identifier(location, output_path):
    existing: set[str] = set()
    name = _auto_typst_name(location, output_path, existing)
    assert TYPST_IDENTIFIER.fullmatch(name)
    assert name in existing


@GENERATED
@given(locations=st.lists(st.text(max_size=60), max_size=12), output_path=st.text(max_size=60))
def test_auto_typst_names_never_collide(locations, output_path):
    existing: set[str] = set()
    names = [_auto_typst_name(location, output_path, existing) for location in locations]
    assert all(TYPST_IDENTIFIER.fullmatch(name) for name in names)
    assert len(set(names)) == len(names)


@GENERATED
@given(value=st.floats())
def test_format_typst_value_renders_any_float(value):
    rendered = format_typst_value(value)
    assert isinstance(rendered, str)
    assert rendered


@GENERATED
@given(
    value=st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.floats(),
        st.text(max_size=200),
    )
)
def test_format_typst_value_renders_any_literal(value):
    rendered = format_typst_value(value)
    assert isinstance(rendered, str)
    assert rendered

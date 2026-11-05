"""Tests for DSSE in-toto Statement envelope export."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from sciproof.capture import capture
from sciproof.cli import main
from sciproof.export import DSSE_PAYLOAD_TYPE, format_dsse, to_dsse


def test_dsse_export_structure(tmp_path: Path):
    out = tmp_path / "model.bin"
    out.write_bytes(b"model-weights-data")
    res = capture(
        name="dsse_exp",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        outputs=["model.bin"],
    )

    envelope = to_dsse(res.manifest_path)
    assert envelope["payloadType"] == DSSE_PAYLOAD_TYPE
    assert envelope["payloadType"] == "application/vnd.in-toto+json"
    assert "signatures" in envelope
    assert isinstance(envelope["signatures"], list)

    raw = base64.b64decode(envelope["payload"].encode("ascii"))
    statement = json.loads(raw.decode("utf-8"))
    assert statement["_type"] == "https://in-toto.io/Statement/v1"
    assert len(statement["subject"]) == 1
    assert statement["subject"][0]["name"] == "model.bin"

    formatted = format_dsse(res.manifest_path)
    assert DSSE_PAYLOAD_TYPE in formatted


def test_dsse_cli_export(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "out.txt").write_text("hello", encoding="utf-8")
    res = capture(
        name="dsse_cli",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        outputs=["out.txt"],
    )

    code = main(["export", str(res.manifest_path), "--format", "dsse"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["payloadType"] == DSSE_PAYLOAD_TYPE

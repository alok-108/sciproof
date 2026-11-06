"""Test calibrate across thread configurations (concurrency drift profiling)."""

from __future__ import annotations

import json
from pathlib import Path

from sciproof.calibrate import calibrate
from sciproof.capture import capture
from sciproof.cli import main


def test_calibrate_threads(tmp_path: Path):
    script = (
        "import os, json\n"
        "threads = int(os.environ.get('OMP_NUM_THREADS', '1'))\n"
        "val = 1.0 / threads\n"
        "with open('out.json', 'w') as f:\n"
        "    json.dump({'val': val}, f)\n"
    )
    (tmp_path / "calc.py").write_text(script, encoding="utf-8")
    (tmp_path / "out.json").write_text(json.dumps({"val": 1.0}), encoding="utf-8")

    res = capture(
        name="drift_prof",
        argv=["python", "calc.py"],
        base=tmp_path,
        numeric_outputs=["out.json"],
    )

    report = calibrate(
        res.manifest_path,
        threads=[1, 2, 4],
        assume_yes=True,
    )
    assert report is not None
    assert report.runs == 3
    assert len(report.exit_codes) == 3


def test_calibrate_cli_threads(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    script = "import pathlib; pathlib.Path('out.txt').write_text('ok\\n')"
    (tmp_path / "run.py").write_text(script, encoding="utf-8")
    (tmp_path / "out.txt").write_text("ok\n", encoding="utf-8")
    res = capture(
        name="cli_thread_exp",
        argv=["python", "run.py"],
        base=tmp_path,
        outputs=["out.txt"],
    )

    code = main(["calibrate", str(res.manifest_path), "--threads", "1,2", "--yes"])
    assert code == 0
    captured = capsys.readouterr()
    assert "RESULT: STABLE" in captured.out


def test_calibrate_cli_rejects_seeds_and_threads(capsys):
    code = main(
        [
            "calibrate",
            "missing.sciproof.json",
            "--seeds",
            "1,2",
            "--threads",
            "1,2,4",
            "--yes",
        ]
    )

    assert code == 3
    assert capsys.readouterr().err == (
        "error: cannot specify both --seeds and --threads: "
        "calibrate variance across seeds OR threads\n"
    )

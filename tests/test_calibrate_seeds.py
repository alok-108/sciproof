"""Tests for Multi-Seed Monte Carlo calibration in sciproof calibrate."""

from __future__ import annotations

import sys

import pytest

from sciproof import cli
from sciproof.calibrate import calibrate


def test_calibrate_with_explicit_seeds(project):
    script = """
import json, os, pathlib
seed = int(os.environ.get("SEED", "0"))
pathlib.Path("out").mkdir(exist_ok=True)
json.dump({"accuracy": 0.80 + 0.01 * seed, "loss": 0.20 - 0.01 * seed}, open("out/res.json", "w"))
pathlib.Path("out/exact.txt").write_text("deterministic")
"""
    (project.root / "model.py").write_text(script)
    project.capture(
        name="exp_mc",
        argv=[sys.executable, "model.py"],
        inputs=["model.py"],
        outputs=["out/exact.txt"],
        numeric_outputs=["out/res.json"],
        rtol=0.25,
    )
    manifest = project.root / "exp_mc.sciproof.json"

    report = calibrate(manifest, seeds=[1, 2, 3], assume_yes=True)
    assert report is not None
    assert report.runs == 3
    assert report.ok is True

    res_output = next(o for o in report.outputs if o.path == "out/res.json")
    assert "$.accuracy" in res_output.mean_std
    mean_acc, std_acc = res_output.mean_std["$.accuracy"]
    # 4 samples (baseline seed 0 + seeds 1, 2, 3): 0.80, 0.81, 0.82, 0.83 -> mean: 0.815
    assert abs(mean_acc - 0.815) < 1e-6
    assert std_acc > 0.01

    # Verify JSON export
    data = report.to_dict()
    assert data["runs"] == 3
    stats = data["outputs"][1]["stats"]
    assert "$.accuracy" in stats
    assert abs(stats["$.accuracy"]["mean"] - 0.815) < 1e-6

    # Verify text report
    text = report.to_text()
    assert "$.accuracy: mean =" in text
    assert "±" in text


def test_calibrate_cli_with_seeds(project, capsys):
    script = """
import json, os, pathlib
seed = int(os.environ.get("SEED", "0"))
pathlib.Path("out").mkdir(exist_ok=True)
json.dump({"metric": 10.0 + seed}, open("out/metric.json", "w"))
"""
    (project.root / "cli_seed.py").write_text(script)
    project.capture(
        name="exp_cli_mc",
        argv=[sys.executable, "cli_seed.py"],
        inputs=["cli_seed.py"],
        outputs=[],
        numeric_outputs=["out/metric.json"],
        rtol=2.0,
    )
    manifest = project.root / "exp_cli_mc.sciproof.json"

    code = cli.main(["calibrate", str(manifest), "--seeds", "5,10,15", "--yes"])
    assert code == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "3 runs of the recorded command" in out
    assert "$.metric: mean =" in out


def test_calibrate_invalid_seeds_reports_usage_error(project, capsys):
    manifest = project.root / "nonexistent.sciproof.json"
    code = cli.main(["calibrate", str(manifest), "--seeds", "42", "--yes"])
    assert code == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "at least 2 seeds" in err
    assert "--seeds" in err
    assert "--runs" not in err

    code = cli.main(["calibrate", str(manifest), "--seeds", "not_a_number", "--yes"])
    assert code == cli.EXIT_ERROR
    assert "comma-separated list of integers" in capsys.readouterr().err

    too_many = ",".join(str(i) for i in range(51))
    code = cli.main(["calibrate", str(manifest), "--seeds", too_many, "--yes"])
    assert code == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "cannot specify more than 50 seeds" in err
    assert "--seeds" in err


def test_calibrate_conflicting_seeds_and_runs_rejected(project, capsys):
    manifest = project.root / "nonexistent.sciproof.json"
    code = cli.main(["calibrate", str(manifest), "--seeds", "1,2,3", "--runs", "10", "--yes"])
    assert code == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "conflicting options: --runs (10) does not match number of seeds (3)" in err


def test_calibrate_python_api_validates_seeds_and_conflicts(project):
    from sciproof.errors import UsageError

    manifest = project.root / "nonexistent.sciproof.json"
    with pytest.raises(UsageError, match="--seeds must specify at least 2 seeds"):
        calibrate(manifest, seeds=[42], assume_yes=True)

    with pytest.raises(UsageError, match="--seeds cannot specify more than 50 seeds"):
        calibrate(manifest, seeds=list(range(51)), assume_yes=True)

    with pytest.raises(
        UsageError,
        match=r"conflicting options: --runs \(10\) does not match number of seeds \(3\)",
    ):
        calibrate(manifest, runs=10, seeds=[1, 2, 3], assume_yes=True)

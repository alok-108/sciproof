"""``sciproof calibrate``: how much do the outputs of the recorded command vary between runs?"""

from __future__ import annotations

import json
import os
import sys

import pytest

from sciproof import cli
from sciproof.calibrate import calibrate
from sciproof.errors import ConfirmationRequired, UsageError

# Each run adds 1e-7 to the number (a counter file in the working directory makes the "noise"
# reproducible); `exact` is different in every run only when asked.
NOISY = r"""\
import json, os, pathlib, sys
n = len(pathlib.Path("count.txt").read_text().splitlines()) if os.path.exists("count.txt") else 0
with open("count.txt", "a") as handle:
    handle.write("x\n")
step = float(os.environ.get("STEP", "1e-7"))
os.makedirs("out", exist_ok=True)
json.dump({"loss": 1.0 + n * step, "label": "ok", "n": 3}, open("out/m.json", "w"))
pathlib.Path("out/e.txt").write_text(str(n) if os.environ.get("VARY") else "same")
"""


@pytest.fixture
def noisy(project, monkeypatch):
    (project.root / "noisy.py").write_text(NOISY)
    monkeypatch.setenv("PYTHONHASHSEED", "0")

    def make(**overrides):
        options = {
            "argv": [sys.executable, "noisy.py"],
            "inputs": ["noisy.py"],
            "outputs": ["out/e.txt"],
            "numeric_outputs": ["out/m.json"],
            "rtol": 1e-3,
        }
        options.update(overrides)
        (project.root / "count.txt").unlink(missing_ok=True)
        project.capture(**options)
        (project.root / "count.txt").unlink(missing_ok=True)
        return project

    return make


def test_noise_inside_the_tolerance_is_stable(noisy):
    project = noisy()
    report = calibrate(project.manifest, runs=3, assume_yes=True)
    numeric = next(o for o in report.outputs if o.path == "out/m.json")
    assert report.ok and numeric.ok and numeric.suggestion is None
    assert 0 < numeric.max_rel < 1e-3
    assert "inside the recorded tolerance" in numeric.message
    assert report.to_text().endswith("RESULT: STABLE")


def test_noise_outside_the_tolerance_suggests_one_that_covers_it(noisy):
    project = noisy(rtol=1e-12)
    report = calibrate(project.manifest, runs=4, assume_yes=True)
    numeric = next(o for o in report.outputs if o.path == "out/m.json")
    assert not report.ok and not numeric.ok
    assert "outside the recorded tolerance" in numeric.message
    assert numeric.suggestion.startswith("--tolerance out/m.json 'rtol=")
    suggested = float(numeric.suggestion.split("rtol=")[1].rstrip("'"))
    assert suggested >= numeric.max_rel * 10  # ten times the spread, rounded up
    assert report.to_text().endswith("RESULT: VARIES")


def test_the_suggested_tolerance_makes_the_same_runs_stable(noisy):
    first = noisy(rtol=1e-12)
    numeric = calibrate(first.manifest, runs=4, assume_yes=True).outputs[1]
    assert numeric.suggestion
    rtol = float(numeric.suggestion.split("rtol=")[1].rstrip("'"))
    second = noisy(rtol=rtol, force=True)  # captured again with the suggestion
    assert calibrate(second.manifest, runs=4, assume_yes=True).ok


def test_an_exact_output_that_differs_between_runs_is_reported(noisy, monkeypatch):
    project = noisy()
    monkeypatch.setenv("VARY", "1")
    report = calibrate(project.manifest, runs=3, assume_yes=True)
    exact = next(o for o in report.outputs if o.path == "out/e.txt")
    assert not exact.ok and "not deterministic" in exact.message
    assert "--output-numeric" in exact.message


def test_identical_runs_are_reported_as_identical(noisy, monkeypatch):
    project = noisy()
    monkeypatch.setenv("STEP", "0")
    (project.root / "count.txt").unlink(missing_ok=True)
    report = calibrate(project.manifest, runs=2, assume_yes=True)
    assert all("identical" in o.message for o in report.outputs)


def test_the_working_tree_and_the_manifest_are_left_as_they_were(noisy):
    project = noisy()
    manifest_before = project.manifest.read_bytes()
    out_before = (project.root / "out" / "m.json").read_bytes()
    exact_before = (project.root / "out" / "e.txt").read_bytes()
    calibrate(project.manifest, runs=3, assume_yes=True)
    assert project.manifest.read_bytes() == manifest_before
    assert (project.root / "out" / "m.json").read_bytes() == out_before
    assert (project.root / "out" / "e.txt").read_bytes() == exact_before


def test_outputs_that_did_not_exist_before_are_removed_again(noisy):
    project = noisy()
    (project.root / "out" / "m.json").unlink()
    calibrate(project.manifest, runs=2, assume_yes=True)
    assert not (project.root / "out" / "m.json").exists()


def test_a_changed_exit_code_is_reported(noisy):
    project = noisy()
    data = json.loads(project.manifest.read_text())
    data["execution"]["exit_code"] = 5  # recorded 5, the runs exit with 0
    project.manifest.write_text(json.dumps(data))
    report = calibrate(project.manifest, runs=2, assume_yes=True)
    assert not report.ok and "exit code" in report.to_text()


def test_nothing_runs_without_confirmation(noisy):
    project = noisy()
    with pytest.raises(ConfirmationRequired):
        calibrate(project.manifest, runs=2)
    assert not (project.root / "count.txt").exists()
    assert calibrate(project.manifest, runs=2, confirm=lambda prompt: False) is None
    assert not (project.root / "count.txt").exists()


@pytest.mark.parametrize("runs", [0, 1, 51, -3])
def test_the_number_of_runs_is_bounded(noisy, runs):
    with pytest.raises(UsageError, match="between 2 and 50"):
        calibrate(noisy().manifest, runs=runs, assume_yes=True)


def test_cli_calibrate(noisy, capsys):
    project = noisy(rtol=1e-12)
    code = cli.main(["calibrate", project.manifest.name, "--runs", "3", "--yes"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_FAIL
    assert "suggestion: --tolerance out/m.json" in out and "RESULT: VARIES" in out


def test_cli_calibrate_without_a_terminal_refuses(noisy, capsys, monkeypatch):
    project = noisy()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert cli.main(["calibrate", project.manifest.name]) == cli.EXIT_ERROR
    assert "refusing to execute" in capsys.readouterr().err


@pytest.mark.parametrize("flags", [["--format", "json"], ["--json"]])
@pytest.mark.parametrize("stable", [True, False])
def test_cli_calibration_json_is_one_object_with_numeric_suggestions(noisy, capsys, flags, stable):
    project = noisy(rtol=1e-3 if stable else 1e-12)
    before = project.manifest.read_bytes()
    outputs = {p: (project.root / p).read_bytes() for p in ["out/m.json", "out/e.txt"]}

    code = cli.main(["calibrate", project.manifest.name, "--runs", "3", "--yes", *flags])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert code == (cli.EXIT_OK if stable else cli.EXIT_FAIL)
    assert report["schema"] == "sciproof.calibration"
    assert report["schema_version"] == "1.0"
    assert report["name"] == "exp" and report["runs"] == 3
    assert report["recorded_exit_code"] == 0 and report["exit_codes"] == [0, 0, 0]
    assert report["result"] == ("STABLE" if stable else "VARIES")
    numeric = next(o for o in report["outputs"] if o["mode"] == "numeric")
    assert numeric["ok"] is stable and numeric["max_abs"] > 0 and numeric["max_rel"] > 0
    if not stable:
        assert isinstance(numeric["suggestion"]["rtol"], float)
        assert numeric["suggestion"]["rtol"] >= numeric["max_rel"] * 10
    else:
        assert "suggestion" not in numeric
    exact = next(o for o in report["outputs"] if o["mode"] == "exact")
    assert exact["ok"] and "max_abs" not in exact
    assert report["notes"] and captured.err == ""
    assert project.manifest.read_bytes() == before
    assert all((project.root / p).read_bytes() == data for p, data in outputs.items())


def test_json_suggestion_agrees_with_the_existing_text_report(noisy):
    report = calibrate(noisy(rtol=1e-12).manifest, runs=3, assume_yes=True)
    numeric = report.outputs[1]
    suggested = report.to_dict()["outputs"][1]["suggestion"]["rtol"]
    assert suggested == float(numeric.suggestion.split("rtol=")[1].rstrip("'"))
    assert numeric.suggestion in report.to_text()


@pytest.mark.parametrize("flags", [["--format", "json"], ["--json"]])
def test_json_decline_and_error_keep_stdout_empty(noisy, capsys, monkeypatch, flags):
    project = noisy()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    assert cli.main(["calibrate", project.manifest.name, "--runs", "2", *flags]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "declined" in captured.err
    assert not (project.root / "count.txt").exists()
    assert cli.main(["calibrate", project.manifest.name, "--runs", "1", *flags]) == 3
    captured = capsys.readouterr()
    assert captured.out == "" and "between 2 and 50" in captured.err
    assert not (project.root / "count.txt").exists()


def test_conflicting_calibration_formats_are_rejected_before_running(noisy, capsys):
    project = noisy()
    assert (
        cli.main(["calibrate", project.manifest.name, "--yes", "--json", "--format", "text"]) == 3
    )
    captured = capsys.readouterr()
    assert captured.out == "" and "conflicts" in captured.err
    assert not (project.root / "count.txt").exists()


@pytest.mark.parametrize("spread", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_json_is_an_error_not_a_partial_report(capsys, monkeypatch, spread):
    from sciproof.calibrate import CalibrationReport, OutputSpread

    report = CalibrationReport(
        "exp",
        2,
        0,
        [0, 0],
        [OutputSpread("out/m.json", "numeric", False, "varies", max_abs=spread)],
    )
    monkeypatch.setattr(cli, "calibrate", lambda *args, **kwargs: report)
    assert cli.main(["calibrate", "exp.sciproof.json", "--yes", "--json"]) == 3
    captured = capsys.readouterr()
    assert captured.out == "" and "non-finite" in captured.err


def test_json_confirmation_prompt_stays_on_stderr(noisy, capsys, monkeypatch):
    project = noisy()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert cli.main(["calibrate", project.manifest.name, "--runs", "2", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["result"] == "STABLE"
    assert "EXECUTE" in captured.err and "noisy.py" in captured.err


# ---------------------------------------------------------------- edge cases


def test_a_numeric_output_absent_during_runs_is_reported(noisy):
    """A run that never produces its declared numeric output is variation, not a crash."""
    project = noisy()
    (project.root / "noisy.py").write_text("pass\n")  # the runs produce nothing at all
    report = calibrate(project.manifest, runs=2, assume_yes=True)
    numeric = next(o for o in report.outputs if o.path == "out/m.json")
    assert not numeric.ok and "absent in some runs" in numeric.message


def test_an_unparseable_numeric_output_is_reported_without_crashing(noisy):
    """Garbage in a numeric output is reported, not raised."""
    project = noisy()
    (project.root / "noisy.py").write_text(
        "import os, pathlib\n"
        "os.makedirs('out', exist_ok=True)\n"
        "pathlib.Path('out/m.json').write_text('not json at all')\n"
        "pathlib.Path('out/e.txt').write_text('same')\n"
    )
    report = calibrate(project.manifest, runs=2, assume_yes=True)
    numeric = next(o for o in report.outputs if o.path == "out/m.json")
    assert not numeric.ok and "cannot be read:" in numeric.message


@pytest.mark.skipif(sys.platform == "win32", reason="os.mkfifo is POSIX-only")
def test_a_directory_tree_that_cannot_be_hashed_fingerprints_as_none(tmp_path):
    """An unhashable tree (here a named pipe) must not crash the fingerprint."""
    from sciproof.calibrate import _fingerprint

    os.mkfifo(tmp_path / "pipe")  # neither a regular file nor a directory: TreeError
    assert _fingerprint(tmp_path, directory=True) is None


def test_a_location_missing_from_some_runs_is_flagged():
    from sciproof.calibrate import _spread

    samples = [[("loss", 1.0), ("extra", 2.0)], [("loss", 1.0)]]
    _, _, _, problem = _spread(samples, 1e-3, 0.0)
    assert problem == "extra is not present in every run"


def test_a_location_nan_in_some_runs_only_is_flagged():
    from sciproof.calibrate import _spread

    _, _, _, problem = _spread([[("x", float("nan"))], [("x", 1.0)]], 1e-3, 0.0)
    assert problem == "x is NaN in some runs only"


def test_a_location_infinite_in_some_runs_only_is_flagged():
    from sciproof.calibrate import _spread

    _, _, _, problem = _spread([[("x", float("inf"))], [("x", 100.0)]], 1e-3, 0.0)
    assert problem == "x is infinite in some runs only"


# ------------------------------------------------------- exact-output verdicts


def test_an_exact_output_missing_after_some_runs_is_not_ok(tmp_path):
    from sciproof.calibrate import _judge

    item = {"path": "out.txt", "compare": {"mode": "exact"}}
    spread = _judge(tmp_path, item, [None, "hash1"], [])
    assert not spread.ok and spread.message == "missing after some runs"


def test_an_exact_output_with_different_hashes_is_not_deterministic(tmp_path):
    from sciproof.calibrate import _judge

    item = {"path": "out.txt", "compare": {"mode": "exact"}}
    spread = _judge(tmp_path, item, ["hash1", "hash2"], [])
    assert not spread.ok and "not deterministic" in spread.message
    assert "--output-numeric" in spread.message  # tells the author how to declare varying numbers


def test_an_exact_output_differing_from_the_recording_is_not_ok(tmp_path):
    from sciproof.calibrate import _judge

    item = {"path": "out.txt", "sha256": "recorded", "compare": {"mode": "exact"}}
    spread = _judge(tmp_path, item, ["new_hash", "new_hash"], [])
    assert not spread.ok
    assert spread.message == "identical in every run, but different from the recorded one"

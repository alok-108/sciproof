"""Tests for SARIF 2.1.0 output in verification reports."""

from __future__ import annotations

import json

import pytest

from sciproof import cli
from sciproof.sarif import SARIF_SCHEMA, SARIF_VERSION, format_report_sarif, report_sarif
from sciproof.verify import Check, Report, Status, verify


def test_sarif_pass_report(project, capsys):
    project.capture()
    report = verify(project.manifest)
    sarif = report_sarif(report)

    assert sarif["$schema"] == SARIF_SCHEMA
    assert sarif["version"] == SARIF_VERSION
    assert len(sarif["runs"]) == 1
    run = sarif["runs"][0]
    assert run["tool"]["driver"]["name"] == "sciproof"
    assert run["results"] == []

    # Check CLI output
    code = cli.main(["verify", "exp.sciproof.json", "--format", "sarif"])
    assert code == cli.EXIT_OK
    cli_out = json.loads(capsys.readouterr().out)
    assert cli_out["runs"][0]["results"] == []


def test_sarif_fail_report(project, capsys):
    project.capture()
    (project.root / "out" / "exact.txt").write_text("modified")
    report = verify(project.manifest)
    sarif = report_sarif(report)

    results = sarif["runs"][0]["results"]
    assert len(results) >= 1
    fail_res = [r for r in results if r["ruleId"] == "SP001"]
    assert fail_res
    assert fail_res[0]["level"] == "error"
    assert "out/exact.txt" in fail_res[0]["message"]["text"]
    assert fail_res[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == (
        "out/exact.txt"
    )

    code = cli.main(["verify", "exp.sciproof.json", "--format", "sarif"])
    assert code == cli.EXIT_FAIL
    cli_out = json.loads(capsys.readouterr().out)
    assert any(r["ruleId"] == "SP001" for r in cli_out["runs"][0]["results"])


def test_format_report_sarif_helper(project):
    project.capture()
    report = verify(project.manifest)
    text = format_report_sarif(report)
    assert text.endswith("\n")
    assert json.loads(text)["version"] == "2.1.0"


def test_sarif_inconclusive_report():
    report = Report(
        name="exp",
        rerun_requested=False,
        checks=[Check(Status.INCONCLUSIVE, "output out/missing.txt", "missing output")],
    )
    results = report_sarif(report)["runs"][0]["results"]
    assert len(results) == 1
    assert results[0]["ruleId"] == "SP002"
    assert results[0]["level"] == "warning"


def test_sarif_warning_report():
    report = Report(
        name="exp",
        rerun_requested=False,
        warnings=["some verification warning"],
    )
    results = report_sarif(report)["runs"][0]["results"]
    assert len(results) == 1
    assert results[0]["ruleId"] == "SP003"
    assert results[0]["level"] == "warning"
    assert "some verification warning" in results[0]["message"]["text"]
    assert len(results[0]["locations"]) == 1
    assert (
        results[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        == "exp.sciproof.json"
    )


@pytest.mark.parametrize(
    ("check", "expected_uri"),
    [
        (
            Check(
                Status.FAIL,
                "rerun output w.npz",
                "differs outside tolerance",
                evidence={"path": "w.npz"},
            ),
            "w.npz",
        ),
        (
            Check(Status.FAIL, "exit code", "exited 1", evidence={"kind": "exit_code"}),
            "my_exp.sciproof.json",
        ),
        (
            Check(
                Status.FAIL,
                "manifest signature",
                "bad signature",
                evidence={"kind": "signature"},
            ),
            "my_exp.sciproof.json",
        ),
        (
            Check(
                Status.FAIL,
                "policy manifest.max_age_days",
                "too old",
                evidence={"kind": "policy"},
            ),
            "my_exp.sciproof.json",
        ),
        (
            Check(
                Status.FAIL,
                "claim m.json: $.acc >= 0.9",
                "violated",
                evidence={"output": "m.json"},
            ),
            "m.json",
        ),
    ],
)
def test_sarif_subjects_resolve_to_clean_file_uris(check, expected_uri):
    report = Report(
        name="my_exp",
        rerun_requested=True,
        checks=[check],
        warnings=["warning note"],
    )
    results = report_sarif(report)["runs"][0]["results"]
    for result in results:
        assert len(result["locations"]) == 1
        uri = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        assert not uri.startswith(
            ("rerun output ", "exit code", "manifest signature", "policy ", "claim ")
        )
        assert uri in {expected_uri, "my_exp.sciproof.json"}


def test_sarif_uris_are_repo_relative_in_subdirectories(tmp_path):
    repo_root = tmp_path / "repo"
    (repo_root / ".git").mkdir(parents=True)
    sub_dir = repo_root / "experiments" / "run1"
    sub_dir.mkdir(parents=True)
    manifest_file = sub_dir / "exp.sciproof.json"
    manifest_file.write_text("{}", encoding="utf-8")

    report = Report(
        name="exp",
        rerun_requested=False,
        manifest_path=manifest_file,
        checks=[
            Check(
                Status.FAIL,
                "output results/out.txt",
                "hash mismatch",
                evidence={"path": "results/out.txt"},
            ),
            Check(Status.FAIL, "exit code", "failed", evidence={"kind": "exit_code"}),
        ],
        warnings=["disk low"],
    )
    results = report_sarif(report)["runs"][0]["results"]
    uris = [r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for r in results]
    assert uris[0] == "experiments/run1/results/out.txt"
    assert uris[1] == "experiments/run1/exp.sciproof.json"
    assert uris[2] == "experiments/run1/exp.sciproof.json"

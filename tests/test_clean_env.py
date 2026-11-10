"""``verify --rerun --clean-env``: the recorded command gets a reduced environment."""

from __future__ import annotations

import json
import sys

import pytest

from sciproof import cli
from sciproof.environment import CLEAN_ENV_BASE, ENV_ALLOWLIST, clean_environment
from sciproof.errors import UsageError
from sciproof.verify import Status, report_to_dict, verify

SECRET = "SCIPROOF_TEST_SECRET"
SCRIPT = (
    f"import os, pathlib; pathlib.Path('seen.txt').write_text(os.environ.get('{SECRET}', 'unset'))"
)


def _capture_env_probe(project):
    """Capture a command that writes the value of SCIPROOF_TEST_SECRET into seen.txt."""
    return project.capture(
        name="env",
        argv=[sys.executable, "-c", SCRIPT],
        inputs=["data.json"],
        outputs=["seen.txt"],
        numeric_outputs=[],
    )


def _manifest(project):
    return project.root / "env.sciproof.json"


# ---------------------------------------------------------------- behaviour


def test_a_default_rerun_hands_the_whole_environment_to_the_command(project, monkeypatch):
    """Documents the risk (SECURITY.md): without --clean-env a rerun sees every variable."""
    monkeypatch.delenv(SECRET, raising=False)
    _capture_env_probe(project)
    monkeypatch.setenv(SECRET, "hunter2")
    report = verify(_manifest(project), rerun=True, assume_yes=True)
    assert report.status is Status.FAIL  # seen.txt now contains the secret, so it differs
    moved = project.root / report.rerun_outputs_dir / "seen.txt"
    assert moved.read_text() == "hunter2"


def test_clean_env_withholds_variables_that_were_not_passed(project, monkeypatch):
    monkeypatch.delenv(SECRET, raising=False)
    _capture_env_probe(project)
    monkeypatch.setenv(SECRET, "hunter2")
    report = verify(_manifest(project), rerun=True, assume_yes=True, clean_env=True)
    assert report.status is Status.PASS, report.checks  # the command saw 'unset'
    assert report.clean_env is True
    assert SECRET not in report.env_passed
    assert any("reduced environment" in note for note in report.info)


def test_pass_env_passes_exactly_the_named_variable(project, monkeypatch):
    monkeypatch.setenv(SECRET, "hunter2")
    _capture_env_probe(project)  # seen.txt == 'hunter2'
    without = verify(_manifest(project), rerun=True, assume_yes=True, clean_env=True)
    assert without.status is Status.FAIL  # the command saw 'unset'
    with_it = verify(
        _manifest(project), rerun=True, assume_yes=True, clean_env=True, pass_env=[SECRET]
    )
    assert with_it.status is Status.PASS
    assert SECRET in with_it.env_passed


def test_the_command_still_starts_with_the_reduced_environment(project, monkeypatch):
    """PATH, SYSTEMROOT etc. are kept, otherwise most programs (Python on Windows) cannot start."""
    monkeypatch.delenv(SECRET, raising=False)
    project.capture()
    monkeypatch.setenv("SCIPROOF_TEST_EPS", "0")
    report = verify(project.manifest, rerun=True, assume_yes=True, clean_env=True)
    assert report.status is Status.PASS, report.checks
    assert report.rerun_executed is True


def test_numerics_related_variables_are_passed_through(project, monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    environment, unset = clean_environment()
    assert environment["OMP_NUM_THREADS"] == "3"
    assert unset == []


def test_values_never_appear_in_the_report(project, monkeypatch):
    monkeypatch.setenv(SECRET, "hunter2-value")
    _capture_env_probe(project)
    report = verify(
        _manifest(project), rerun=True, assume_yes=True, clean_env=True, pass_env=[SECRET]
    )
    text = json.dumps(report_to_dict(report))
    assert "hunter2-value" not in text
    assert SECRET in text  # the name is listed, the value is not


def test_requested_but_unset_variables_are_noted(project, monkeypatch):
    monkeypatch.delenv(SECRET, raising=False)
    _capture_env_probe(project)
    report = verify(
        _manifest(project), rerun=True, assume_yes=True, clean_env=True, pass_env=[SECRET]
    )
    assert any(SECRET in note and "not set" in note for note in report.info)


def test_the_confirmation_prompt_says_which_environment_the_command_gets(project, monkeypatch):
    monkeypatch.delenv(SECRET, raising=False)
    _capture_env_probe(project)
    prompts: list[str] = []

    def decline(prompt: str) -> bool:
        prompts.append(prompt)
        return False

    verify(_manifest(project), rerun=True, confirm=decline)
    verify(_manifest(project), rerun=True, clean_env=True, confirm=decline)
    assert "ALL of your environment variables" in prompts[0]
    assert "reduced environment" in prompts[1]
    assert "ALL of your environment variables" not in prompts[1]


# ---------------------------------------------------------------- misuse


def test_clean_env_without_rerun_is_a_usage_error(project):
    project.capture()
    with pytest.raises(UsageError, match="only applies to --rerun"):
        verify(project.manifest, clean_env=True)


def test_pass_env_without_clean_env_is_a_usage_error(project):
    project.capture()
    with pytest.raises(UsageError, match="needs --clean-env"):
        verify(project.manifest, rerun=True, assume_yes=True, pass_env=["X"])


@pytest.mark.parametrize("name", ["", "A=B", "A B", "1X", "A\x00", "ä"])
def test_invalid_variable_names_are_refused(name):
    with pytest.raises(UsageError, match="not a valid environment variable name"):
        clean_environment([name])


# ---------------------------------------------------------------- the environment builder


def test_clean_environment_keeps_only_the_base_the_allowlist_and_requested_names():
    source = {
        "PATH": "/bin",
        "HOME": "/home/me",
        "AWS_SECRET_ACCESS_KEY": "s3cr3t",
        "OMP_NUM_THREADS": "2",
        "WANTED": "yes",
    }
    environment, unset = clean_environment(["WANTED", "MISSING"], environ=source)
    assert environment == {"PATH": "/bin", "OMP_NUM_THREADS": "2", "WANTED": "yes"}
    assert unset == ["MISSING"]


def test_clean_environment_has_no_home_directory_or_credentials_by_default():
    assert "HOME" not in CLEAN_ENV_BASE
    assert "USERPROFILE" not in CLEAN_ENV_BASE
    assert not any("TOKEN" in name or "KEY" in name or "SECRET" in name for name in CLEAN_ENV_BASE)
    assert set(ENV_ALLOWLIST).isdisjoint({"AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN"})


@pytest.mark.skipif(sys.platform != "win32", reason="Windows variable names are case-insensitive")
def test_names_are_matched_case_insensitively_on_windows():
    environment, _ = clean_environment(["myvar"], environ={"MyVar": "1", "Path": "C:\\x"})
    assert environment == {"MyVar": "1", "Path": "C:\\x"}


def test_clean_environment_case_folding_and_deduplication():
    source = {"Path": "C:\\Windows", "MY_VAR": "val"}
    if sys.platform == "win32":
        env, unset = clean_environment(["path", "Path", "my_var", "UNSET", "unset"], environ=source)
        assert env["Path"] == "C:\\Windows"
        assert env["MY_VAR"] == "val"
        assert unset == ["UNSET"]
    else:
        env, unset = clean_environment(["WANTED", "wanted"], environ={"WANTED": "1", "wanted": "2"})
        assert env["WANTED"] == "1"
        assert env["wanted"] == "2"


# ---------------------------------------------------------------- command line


def test_cli_clean_env_and_json_fields(project, monkeypatch, capsys):
    monkeypatch.delenv(SECRET, raising=False)
    _capture_env_probe(project)
    monkeypatch.setenv(SECRET, "hunter2")
    code = cli.main(["verify", "env.sciproof.json", "--rerun", "--yes", "--clean-env", "--json"])
    assert code == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["clean_env"] is True
    assert "PATH" in data["env_passed"] or "Path" in data["env_passed"]
    assert SECRET not in data["env_passed"]


def test_cli_rejects_clean_env_without_rerun(project, capsys):
    project.capture()
    assert cli.main(["verify", "exp.sciproof.json", "--clean-env"]) == cli.EXIT_ERROR
    assert "only applies to --rerun" in capsys.readouterr().err

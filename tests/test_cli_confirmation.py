"""Exercise the real CLI confirmation boundary before a recorded command runs."""

from __future__ import annotations

import sys

import pytest

from sciproof import cli


@pytest.fixture(
    params=[
        (["verify", "exp.sciproof.json", "--rerun"], 1),
        (["update", "exp.sciproof.json"], 1),
        (["calibrate", "exp.sciproof.json", "--runs", "2"], 2),
        (["badge", "exp.sciproof.json", "--rerun"], 1),
    ],
    ids=["verify", "update", "calibrate", "badge"],
)
def confirmed_command(request):
    return request.param


def _evidence(project):
    return {
        path.relative_to(project.root): path.read_bytes()
        for path in [project.manifest, *(project.root / "exp.sciproof.d").rglob("*")]
        if path.is_file()
    }


@pytest.mark.parametrize(
    "answer, accepted",
    [
        ("yes", True),
        (" YES ", True),
        ("y", False),
        ("no", False),
        ("", False),
        ("yes please", False),
    ],
)
def test_only_an_explicit_yes_executes_the_recorded_command(
    project, confirmed_command, answer, accepted, monkeypatch, capsys
):
    argv, run_delta = confirmed_command
    project.capture()
    runs = project.runs()
    evidence = _evidence(project)
    prompts = []
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def reply(prompt):
        prompts.append(prompt)
        return answer

    monkeypatch.setattr("builtins.input", reply)

    result = cli.main(argv)

    assert result == (cli.EXIT_OK if accepted else cli.EXIT_INCONCLUSIVE)
    assert project.runs() == runs + (run_delta if accepted else 0)
    assert prompts == ["\nExecute this command? Type 'yes' to continue: "]
    assert "experiment.py" in capsys.readouterr().err
    if not accepted:
        assert _evidence(project) == evidence


@pytest.mark.parametrize(
    "exception, message",
    [(EOFError, "error: no confirmation received"), (KeyboardInterrupt, "interrupted")],
)
def test_interrupted_confirmation_executes_nothing_and_keeps_the_evidence(
    project, confirmed_command, exception, message, monkeypatch, capsys
):
    argv, _run_delta = confirmed_command
    project.capture()
    runs = project.runs()
    evidence = _evidence(project)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def interrupt(prompt):
        raise exception

    monkeypatch.setattr("builtins.input", interrupt)

    assert cli.main(argv) == cli.EXIT_ERROR == 3
    assert message in capsys.readouterr().err
    assert project.runs() == runs
    assert _evidence(project) == evidence

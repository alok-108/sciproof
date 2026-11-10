from __future__ import annotations

import subprocess
import sys

import pytest

from sciproof import environment
from sciproof.environment import _looks_like_python, interpreter_info


@pytest.mark.parametrize(
    "name",
    [
        "python",
        "python3",
        "python3.13",
        "python3.13t",
        "pythonw",
        "py",
        "pypy3",
        "PYTHON.EXE",
        "/usr/bin/python3",
        "venv/Scripts/python.exe",
    ],
)
def test_real_interpreter_names_are_recognised(name):
    assert _looks_like_python(name)


@pytest.mark.parametrize(
    "name",
    [
        "python_train.sh",
        "python-config",
        "python3-config",
        "pythonista",
        "./python_run",
        "pyfoo",
        "train.py",
        "echo",
        "",
    ],
)
def test_other_programs_are_not_probed(name):
    assert not _looks_like_python(name)


def test_a_script_named_like_python_is_never_executed(tmp_path, monkeypatch):
    script = tmp_path / "python_train.sh"
    script.write_text("#!/bin/sh\n")
    spawned = []

    def spy(args, *a, **kw):
        spawned.append(args)
        raise OSError("must not be called")

    monkeypatch.setattr(environment.subprocess, "run", spy)
    info, source = interpreter_info(str(script), tmp_path)
    assert spawned == []
    assert source == "sciproof-process"
    assert info["version"].count(".") == 2


def test_the_current_interpreter_is_still_probed(tmp_path):
    info, source = interpreter_info(sys.executable, tmp_path)
    assert source == "command-interpreter"
    assert info["version"].count(".") == 2


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired(cmd="python", timeout=1),
        subprocess.CalledProcessError(returncode=1, cmd="python"),
        OSError("Permission denied"),
    ],
    ids=["timeout", "nonzero-exit", "oserror"],
)
def test_interpreter_probe_failures_return_cleanly(tmp_path, monkeypatch, error):
    fallback = {"version": "fallback"}
    monkeypatch.setattr(environment, "_own_interpreter_info", lambda: fallback)
    calls = []

    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise error

    monkeypatch.setattr(environment.subprocess, "run", fail)

    info, source = interpreter_info(sys.executable, tmp_path)

    assert info is fallback
    assert source == "sciproof-process"
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == environment.PROBE_TIMEOUT_SECONDS
    assert calls[0][1]["check"] is True


def test_relative_virtualenv_interpreter_probed_when_cwd_differs(tmp_path, monkeypatch):
    project_dir = tmp_path / "subproject"
    venv_bin = project_dir / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
    venv_bin.mkdir(parents=True)
    exe_name = "python.exe" if sys.platform == "win32" else "python"
    fake_exe = venv_bin / exe_name
    fake_exe.write_text("#!/bin/sh\n")

    calls = []

    def mock_run(args, *a, **kw):
        calls.append((args, kw))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                '{"version": "3.99.0", "implementation": "CPython", '
                '"byteorder": "little", "packages": []}'
            ),
        )

    monkeypatch.setattr(environment.subprocess, "run", mock_run)

    relative_argv0 = f".venv/{'Scripts' if sys.platform == 'win32' else 'bin'}/{exe_name}"
    info, source = interpreter_info(relative_argv0, project_dir)

    assert source == "command-interpreter"
    assert info["version"] == "3.99.0"
    assert len(calls) == 1
    assert str(fake_exe) in calls[0][0][0]
    assert calls[0][1]["cwd"] == project_dir

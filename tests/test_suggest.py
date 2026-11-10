from __future__ import annotations

import sys

import pytest

from sciproof import cli
from sciproof.capture import capture
from sciproof.errors import CaptureError
from sciproof.suggest import suggest_inputs
from sciproof.verify import Status, verify

RUN = """\
import json
import helper

config = json.load(open("config.json"))
open("out.txt", "w").write(str(helper.VALUE + config["k"]))
"""


def _project(root):
    (root / "run.py").write_text(RUN)
    (root / "helper.py").write_text("VALUE = 1\n")
    (root / "config.json").write_text('{"k": 1}')
    return [sys.executable, "run.py"]


def test_reports_undeclared_python_files_and_data(tmp_path):
    argv = _project(tmp_path)
    found = suggest_inputs(argv, tmp_path, ["run.py", "out.txt"])
    assert found.exit_code == 0
    assert found.inside == ["config.json", "helper.py"]
    assert found.outside == []


def test_declared_files_are_not_reported(tmp_path):
    argv = _project(tmp_path)
    found = suggest_inputs(argv, tmp_path, ["run.py", "helper.py", "config.json", "out.txt"])
    assert found.inside == []


def test_environment_is_restored(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "keep-me")
    monkeypatch.delenv("SCIPROOF_AUDIT_LOG", raising=False)
    suggest_inputs(_project(tmp_path), tmp_path, ["run.py"])
    import os

    assert os.environ["PYTHONPATH"] == "keep-me"
    assert "SCIPROOF_AUDIT_LOG" not in os.environ


def test_cli_prints_suggestions(tmp_path, monkeypatch, capsys):
    argv = _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    code = cli.main(["suggest-inputs", "--input", "run.py", "--output", "out.txt", "--", *argv])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "suggest --input helper.py" in out
    assert "suggest --input config.json" in out


def test_undeclared_dependency_is_invisible_to_plain_verify_but_not_to_rerun(tmp_path):
    """Documents the limit of declared inputs, and why 'suggest-inputs' exists."""
    argv = _project(tmp_path)
    result = capture(name="exp", argv=argv, base=tmp_path, inputs=["run.py"], outputs=["out.txt"])
    (tmp_path / "helper.py").write_text("VALUE = 20\n")  # new size: no stale .pyc
    assert verify(result.manifest_path).status is Status.PASS  # integrity of declared files only
    rerun = verify(result.manifest_path, rerun=True, assume_yes=True)
    assert rerun.status is Status.FAIL  # the output changed, so the rerun notices


# ---------------------------------------------------------------- hardening


def test_a_normal_run_reports_that_the_hook_was_loaded(tmp_path):
    found = suggest_inputs(_project(tmp_path), tmp_path, ["run.py", "out.txt"])
    assert found.hook_loaded is True


def test_python_without_site_never_loads_the_hook_and_says_so(tmp_path):
    """-S skips sitecustomize, so nothing is observed: that must not look like 'all declared'."""
    (tmp_path / "run.py").write_text("print('hello')\n")
    found = suggest_inputs([sys.executable, "-S", "run.py"], tmp_path, ["run.py"])
    assert found.exit_code == 0
    assert found.hook_loaded is False
    assert found.inside == [] and found.outside == []


def test_cli_is_inconclusive_when_the_hook_never_started(tmp_path, monkeypatch, capsys):
    (tmp_path / "run.py").write_text("print('hello')\n")
    monkeypatch.chdir(tmp_path)
    code = cli.main(["suggest-inputs", "--input", "run.py", "--", sys.executable, "-S", "run.py"])
    captured = capsys.readouterr()
    assert code == cli.EXIT_INCONCLUSIVE
    assert "hook never started" in captured.err
    assert "no undeclared files" not in captured.out


def test_the_calling_process_environment_is_never_modified(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("PYTHONPATH", "keep-me")
    before = dict(os.environ)
    # the command sees the hook's variables, this process does not
    (tmp_path / "run.py").write_text(
        "import os; open('seen.txt', 'w').write(os.environ.get('SCIPROOF_AUDIT_LOG', 'none'))\n"
    )
    suggest_inputs([sys.executable, "run.py"], tmp_path, ["run.py", "seen.txt"])
    assert dict(os.environ) == before
    assert (tmp_path / "seen.txt").read_text() != "none"  # the child did get the log variable


def test_sciproof_s_own_files_are_not_suggested(tmp_path):
    snapshots = tmp_path / "exp.sciproof.d"
    snapshots.mkdir()
    (snapshots / "0-result.json").write_text("{}")
    (tmp_path / "exp.sciproof.json").write_text("{}")
    (tmp_path / "run.py").write_text(
        "open('exp.sciproof.d/0-result.json').read(); open('exp.sciproof.json').read()\n"
    )
    found = suggest_inputs([sys.executable, "run.py"], tmp_path, ["run.py"])
    assert found.inside == []


def test_files_inside_a_declared_directory_are_covered(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.csv").write_text("1")
    (tmp_path / "run.py").write_text("open('data/a.csv').read()\n")
    found = suggest_inputs([sys.executable, "run.py"], tmp_path, ["run.py", "data"])
    assert found.inside == []


@pytest.mark.skipif(sys.platform == "win32", reason="Windows cannot create such a name")
def test_a_file_name_with_a_line_break_cannot_corrupt_the_log(tmp_path):
    weird = "we\nird.txt"
    (tmp_path / weird).write_text("x")
    (tmp_path / "run.py").write_text(f"open({weird!r}).read()\n")
    found = suggest_inputs([sys.executable, "run.py"], tmp_path, ["run.py"])
    assert found.inside == [weird]


def test_files_read_outside_the_workspace_are_reported_as_outside(tmp_path):
    """Undeclared reads split into ``inside`` and ``outside``; here only ``outside`` applies."""
    outside_dir = tmp_path.parent / (tmp_path.name + "-outside")
    outside_dir.mkdir()
    ext_file = outside_dir / "external_data.txt"
    ext_file.write_text("sample")

    (tmp_path / "run.py").write_text(f"open({str(ext_file)!r}).read()\n")
    found = suggest_inputs([sys.executable, "run.py"], tmp_path, ["run.py"])
    assert found.exit_code == 0
    assert found.outside == [str(ext_file.resolve())]
    assert found.inside == []


def test_a_timed_out_run_raises_capture_error_instead_of_reporting_nothing(tmp_path):
    """A command killed by the timeout leaves no trustworthy audit log: fail, don't guess."""
    (tmp_path / "hang.py").write_text("import time; time.sleep(10)\n")
    with pytest.raises(CaptureError, match="nothing was recorded"):
        suggest_inputs([sys.executable, "hang.py"], tmp_path, ["hang.py"], timeout=0.2)


def test_a_cached_pyc_read_is_reported_as_its_python_source(tmp_path):
    """An import served from ``__pycache__`` still points at the source the run depends on."""
    import py_compile

    (tmp_path / "helper_mod.py").write_text("VALUE = 1\n")
    py_compile.compile(str(tmp_path / "helper_mod.py"), doraise=True)

    (tmp_path / "run.py").write_text("import helper_mod\n")
    found = suggest_inputs([sys.executable, "run.py"], tmp_path, ["run.py"])
    assert found.exit_code == 0
    assert found.inside == ["helper_mod.py"]


def test_a_command_that_cannot_be_started_raises_capture_error(tmp_path):
    """A missing executable must surface as CaptureError, not a traceback or empty report."""
    missing = tmp_path / "no-such-executable"
    with pytest.raises(CaptureError):
        suggest_inputs([str(missing)], tmp_path, [])


@pytest.mark.parametrize("ignored_dir", [".venv", "site-packages"])
def test_files_inside_virtualenvs_and_site_packages_are_never_suggested(tmp_path, ignored_dir):
    """Reads under ``.venv``/``site-packages`` are environment noise, not undeclared inputs."""
    noise_dir = tmp_path / ignored_dir / "lib"
    noise_dir.mkdir(parents=True)
    (noise_dir / "data.txt").write_text("x")

    (tmp_path / "run.py").write_text(f"open({str(noise_dir / 'data.txt')!r}).read()\n")
    found = suggest_inputs([sys.executable, "run.py"], tmp_path, ["run.py"])
    assert found.exit_code == 0
    assert found.inside == []
    assert found.outside == []


# ------------------------------------------------------- simulated hook lines


def test_an_unparseable_hook_line_is_skipped_without_crashing(tmp_path):
    """A line in the audit log that is not valid hex is ignored, not a crash or a suggestion."""
    (tmp_path / "run.py").write_text(
        "import os\nopen(os.environ['SCIPROOF_AUDIT_LOG'], 'ab').write(b'not-a-hex-line\\n')\n"
    )
    found = suggest_inputs([sys.executable, "run.py"], tmp_path, ["run.py"])
    assert found.exit_code == 0
    assert found.inside == [] and found.outside == []


def test_a_pyc_hook_line_is_reported_as_its_python_source(tmp_path):
    """A ``__pycache__`` ``.pyc`` observation resolves to the ``.py`` the run depends on.

    The line is fed to the audit log directly (the source is never imported or opened),
    so the resolution is pinned without relying on import machinery internals.
    """
    pkg = tmp_path / "pkg"
    (pkg / "__pycache__").mkdir(parents=True)
    (pkg / "mymod.py").write_text("VALUE = 1\n")
    pyc = pkg / "__pycache__" / "mymod.cpython-313.pyc"
    (tmp_path / "run.py").write_text(
        "import os\n"
        f"open(os.environ['SCIPROOF_AUDIT_LOG'], 'ab').write("
        f"os.fsencode({str(pyc)!r}).hex().encode() + b'\\n')\n"
    )
    found = suggest_inputs([sys.executable, "run.py"], tmp_path, ["run.py"])
    assert found.exit_code == 0
    assert found.inside == ["pkg/mymod.py"]


def test_suggest_inputs_chains_to_existing_sitecustomize(tmp_path, monkeypatch):
    """The injected sitecustomize must chain to any pre-existing sitecustomize on PYTHONPATH."""
    import os

    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "sitecustomize.py").write_text(
        "import builtins\nbuiltins.PROJECT_SETUP = 'done'\n", encoding="utf-8"
    )
    (tmp_path / "data.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / "run.py").write_text(
        "import builtins, json\n"
        "data = open('data.txt').read()\n"
        "setup = getattr(builtins, 'PROJECT_SETUP', 'missing')\n"
        "json.dump({'setup': setup, 'data': data}, open('out.json', 'w'))\n",
        encoding="utf-8",
    )
    existing = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH", str(site_dir) + (os.pathsep + existing if existing else ""))

    found = suggest_inputs([sys.executable, "run.py"], tmp_path, ["run.py"])
    assert found.exit_code == 0
    assert "data.txt" in found.inside
    import json

    output = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert output["setup"] == "done"


def test_record_with_existing_sitecustomize_declares_outputs_stable(tmp_path, monkeypatch):
    """record run 1 (with hook) and run 2 (without) both execute user sitecustomize."""
    import json
    import os

    from sciproof.record import record

    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "sitecustomize.py").write_text(
        "import builtins\nbuiltins.PROJECT_SETUP = 'done'\n", encoding="utf-8"
    )
    (tmp_path / "run.py").write_text(
        "import builtins, json\n"
        "setup = getattr(builtins, 'PROJECT_SETUP', 'missing')\n"
        "json.dump({'setup': setup}, open('out.json', 'w'))\n",
        encoding="utf-8",
    )
    existing = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH", str(site_dir) + (os.pathsep + existing if existing else ""))

    result = record(name="exp", argv=[sys.executable, "run.py"], base=tmp_path)
    assert "out.json" in (result.exact + result.numeric)
    assert not any(path == "out.json" for path, _ in result.excluded)
    output = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert output["setup"] == "done"

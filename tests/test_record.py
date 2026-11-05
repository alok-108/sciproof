"""``sciproof record``: capture without declaring files; the runs decide what is stable."""

from __future__ import annotations

import importlib
import json
import sys

import pytest

from sciproof import cli, load_manifest, verify_manifest
from sciproof.errors import CaptureError, CommandFailed
from sciproof.record import record
from sciproof.runner import CommandInterrupted, CommandNotRunnable, CommandTimeout
from sciproof.verify import Status

# Runs are counted in a file outside the project (so the counter is not itself an output).
SCRIPT = r"""
import json, os, pathlib, random

with open(os.environ["COUNT_FILE"], "a") as handle:
    handle.write("x\n")
n = len(open(os.environ["COUNT_FILE"]).read().splitlines())
data = json.loads(pathlib.Path("data.json").read_text())
pathlib.Path("out").mkdir(exist_ok=True)
pathlib.Path("out/stable.txt").write_text("stable")
json.dump({"v": data["x"] * 2}, open("out/stable.json", "w"))
if os.environ.get("NOISE"):
    json.dump({"loss": 1.0 + n * 1e-7, "label": "ok"}, open("out/noisy.json", "w"))
if os.environ.get("LARGE_NOISE"):
    json.dump({"loss": float(n)}, open("out/large-noise.json", "w"))
if os.environ.get("REWRITE_STATE"):
    state = pathlib.Path("state.json")
    value = json.loads(state.read_text())
    json.dump({"n": value["n"] + 1}, open(state, "w"))
if os.environ.get("RANDOM_TEXT"):
    pathlib.Path("out/random.txt").write_text(str(random.random()))
if os.environ.get("RANDOM_JSON"):
    json.dump({"label": str(random.random())}, open("out/label.json", "w"))
if os.environ.get("ONLY_FIRST") and n == 1:
    pathlib.Path("out/first.txt").write_text("1")
if os.environ.get("FAIL_SECOND") and n == 2:
    open("gone.txt")
"""


@pytest.fixture
def lab(tmp_path, monkeypatch):
    root = tmp_path / "lab"
    root.mkdir()
    (root / "data.json").write_text('{"x": 21}')
    (root / "run.py").write_text(SCRIPT)
    (root / "unrelated.txt").write_text("not read")
    counter = tmp_path / "count.txt"
    monkeypatch.setenv("COUNT_FILE", str(counter))
    for name in (
        "NOISE",
        "LARGE_NOISE",
        "REWRITE_STATE",
        "RANDOM_TEXT",
        "RANDOM_JSON",
        "ONLY_FIRST",
        "FAIL_SECOND",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(root)

    class Lab:
        path = root

        @staticmethod
        def runs():
            return len(counter.read_text().splitlines()) if counter.exists() else 0

        @staticmethod
        def go(**kwargs):
            return record(name="exp", argv=[sys.executable, "run.py"], base=root, **kwargs)

    return Lab


def test_inputs_and_outputs_are_found_without_declaring_anything(lab):
    result = lab.go()
    assert result.inputs == ["data.json", "run.py"]  # unrelated.txt was never read
    assert result.exact == ["out/stable.txt"]
    assert result.numeric == ["out/stable.json"]
    assert result.excluded == []
    manifest = load_manifest(result.capture.manifest_path)
    assert [i["path"] for i in manifest["inputs"]] == ["data.json", "run.py"]
    assert {o["path"]: o["compare"]["mode"] for o in manifest["outputs"]} == {
        "out/stable.txt": "exact",
        "out/stable.json": "numeric",
    }
    assert lab.runs() == 3  # one to look, one to compare, one to capture


def test_npy_and_npz_outputs_are_declared_numeric(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    root = tmp_path / "lab"
    root.mkdir()
    script = (
        "import numpy as np, pathlib; pathlib.Path('out').mkdir(exist_ok=True);"
        "np.save('out/arr.npy', np.array([1.0, 2.0]));"
        "np.savez('out/data.npz', a=np.array([1.0, 2.0]), b=np.array([3.0]))"
    )
    (root / "run.py").write_text(script)
    monkeypatch.chdir(root)
    result = record(name="exp", argv=[sys.executable, "run.py"], base=root)
    assert "out/arr.npy" in result.numeric
    assert "out/data.npz" in result.numeric
    assert "out/arr.npy" not in result.exact
    manifest = load_manifest(result.capture.manifest_path)
    modes = {o["path"]: o["compare"]["mode"] for o in manifest["outputs"]}
    assert modes["out/arr.npy"] == "numeric"
    assert modes["out/data.npz"] == "numeric"


def test_varying_npy_output_gets_a_derived_tolerance(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    root = tmp_path / "lab"
    root.mkdir()
    counter = tmp_path / "count.txt"
    monkeypatch.setenv("COUNT_FILE", str(counter))
    script = (
        "import numpy as np, os, pathlib; pathlib.Path('out').mkdir(exist_ok=True);"
        "count_file = os.environ['COUNT_FILE'];"
        "n = len(open(count_file).read().splitlines()) if os.path.exists(count_file) else 0;"
        "open(count_file, 'a').write('x\\n');"
        "np.save('out/arr.npy', np.array([1.0 + n * 1e-7, 2.0]))"
    )
    (root / "run.py").write_text(script)
    monkeypatch.chdir(root)
    result = record(name="exp", argv=[sys.executable, "run.py"], base=root)
    assert "out/arr.npy" in result.numeric
    assert 1e-9 < result.tolerances["out/arr.npy"]["rtol"] < 1e-3


def test_the_recorded_evidence_verifies_and_reruns(lab):
    result = lab.go()
    assert verify_manifest(result.capture.manifest_path).status is Status.PASS
    rerun = verify_manifest(result.capture.manifest_path, rerun=True, assume_yes=True)
    assert rerun.status is Status.PASS


def test_a_numeric_output_that_varies_gets_a_tolerance_that_covers_it(lab, monkeypatch):
    monkeypatch.setenv("NOISE", "1")
    result = lab.go()
    assert "out/noisy.json" in result.numeric
    rtol = result.tolerances["out/noisy.json"]["rtol"]
    assert 1e-9 < rtol < 1e-3
    compare = {
        o["path"]: o["compare"] for o in load_manifest(result.capture.manifest_path)["outputs"]
    }
    assert compare["out/noisy.json"]["rtol"] == rtol
    assert compare["out/stable.json"]["rtol"] == 1e-9
    assert "--tolerance out/noisy.json" in result.command_line


def test_a_read_and_written_file_is_not_declared_as_an_output(lab, monkeypatch):
    (lab.path / "state.json").write_text('{"n": 0}')
    monkeypatch.setenv("REWRITE_STATE", "1")

    result = lab.go()

    assert "state.json" not in result.exact + result.numeric
    assert ("state.json", "read and written by the command: it is state, not a result") in (
        result.excluded
    )
    manifest = load_manifest(result.capture.manifest_path)
    assert "state.json" not in [output["path"] for output in manifest["outputs"]]


def test_a_numeric_output_with_a_meaningless_tolerance_is_not_declared(lab, monkeypatch):
    monkeypatch.setenv("LARGE_NOISE", "1")

    result = lab.go()

    assert "out/large-noise.json" not in result.numeric
    assert any(
        path == "out/large-noise.json" and "derived rtol" in reason and "1 or more" in reason
        for path, reason in result.excluded
    )


def test_an_output_that_differs_between_identical_runs_is_not_declared(lab, monkeypatch):
    monkeypatch.setenv("RANDOM_TEXT", "1")
    result = lab.go()
    assert [p for p, _ in result.excluded] == ["out/random.txt"]
    assert "not JSON/CSV/TSV" in result.excluded[0][1]
    manifest = load_manifest(result.capture.manifest_path)
    assert "out/random.txt" not in [o["path"] for o in manifest["outputs"]]


def test_a_json_output_whose_text_changes_is_not_declared(lab, monkeypatch):
    monkeypatch.setenv("RANDOM_JSON", "1")
    result = lab.go()
    assert [p for p, _ in result.excluded] == ["out/label.json"]
    assert "not the same value" in result.excluded[0][1]


def test_an_output_written_by_one_run_only_is_not_declared(lab, monkeypatch):
    monkeypatch.setenv("ONLY_FIRST", "1")
    result = lab.go()
    assert [p for p, _ in result.excluded] == ["out/first.txt"]
    assert "not written by the second run" in result.excluded[0][1]


def test_fast_skips_the_second_run_and_says_so(lab):
    result = lab.go(fast=True)
    assert lab.runs() == 2
    assert any("--fast" in n for n in result.notes)
    assert result.excluded == []


def test_the_equivalent_capture_command_is_printed(lab):
    line = lab.go().command_line
    assert line.startswith("sciproof capture --name exp --input data.json --input run.py")
    assert "--output out/stable.txt" in line and "--output-numeric out/stable.json" in line
    assert line.endswith("run.py")


def test_exclude_and_extra_inputs(lab):
    (lab.path / "extra.txt").write_text("hand")
    result = lab.go(inputs=["extra.txt"], exclude=["out/stable.txt"])
    assert "extra.txt" in result.inputs
    assert "out/stable.txt" not in result.exact


def test_files_in_ignored_directories_are_not_outputs(lab):
    script = (
        "import pathlib, json; pathlib.Path('.venv').mkdir(exist_ok=True);"
        "pathlib.Path('.venv/x').write_text('1'); json.dump({}, open('r.json', 'w'))"
    )
    result = record(name="e", argv=[sys.executable, "-c", script], base=lab.path)
    assert result.numeric == ["r.json"]


def test_a_command_that_writes_nothing_cannot_be_recorded(lab):
    with pytest.raises(CaptureError, match="created or changed no file"):
        record(name="e", argv=[sys.executable, "-c", "pass"], base=lab.path)


def test_a_failing_command_is_not_recorded(lab):
    with pytest.raises(CaptureError, match=r"first run of the command failed \(exit status 3\)"):
        record(name="e", argv=[sys.executable, "-c", "raise SystemExit(3)"], base=lab.path)
    assert not (lab.path / "e.sciproof.json").exists()


def test_a_failing_first_run_shows_the_end_of_its_stderr_escaped(lab):
    script = (
        "import sys\n"
        "for i in range(30): print('line', i, file=sys.stderr)\n"
        "print('ValueError: \\x1b[31mbad', file=sys.stderr)\n"
        "raise SystemExit(3)"
    )
    with pytest.raises(CommandFailed) as caught:
        record(name="e", argv=[sys.executable, "-c", script], base=lab.path)
    assert "first run" in str(caught.value) and "exit status 3" in str(caught.value)
    details = caught.value.details
    assert details[0] == "  last lines of its standard error:"
    assert details[-2] == "    ValueError: \\x1b[31mbad"  # the escape sequence is not printed raw
    assert details[1] == "    line 21" and len(details) == 12  # the last ten lines only
    assert "record needs a run that succeeds" in details[-1]


def test_a_failing_run_without_stderr_shows_no_empty_section(lab):
    with pytest.raises(CommandFailed) as caught:
        record(name="e", argv=[sys.executable, "-c", "raise SystemExit(2)"], base=lab.path)
    assert caught.value.details == ["  (record needs a run that succeeds)"]


def test_a_failing_second_run_says_so_and_shows_the_end_of_its_stderr(lab, monkeypatch):
    monkeypatch.setenv("FAIL_SECOND", "1")
    with pytest.raises(CommandFailed) as caught:
        lab.go()
    message = str(caught.value)
    assert message.startswith("the second run of the command failed (exit status 1)")
    assert "nothing was recorded" in message
    assert "FileNotFoundError" in caught.value.details[-2]
    assert "gone.txt" in caught.value.details[-2]
    assert "not repeatable" in caught.value.details[-1]
    assert lab.runs() == 2
    assert not (lab.path / "exp.sciproof.json").exists()


def test_an_existing_manifest_stops_it_before_anything_runs(lab):
    lab.go()
    before = lab.runs()
    with pytest.raises(CaptureError, match="already exists"):
        lab.go()
    assert lab.runs() == before
    assert lab.go(force=True)


def test_a_command_that_is_not_python_gets_a_note_and_keeps_explicit_inputs(lab):
    script = "import json; json.dump({'a': 1}, open('o.json', 'w'))"
    result = record(
        name="e",
        argv=[sys.executable, "-I", "-c", script],  # -I: the audit hook cannot start
        base=lab.path,
        inputs=["data.json"],
    )
    assert result.inputs == ["data.json"]
    assert any("no input could be detected" in n for n in result.notes)


def test_no_leftover_files_of_the_probe_runs_are_declared_or_missing(lab):
    result = lab.go()
    assert (lab.path / "out" / "stable.txt").read_text() == "stable"  # the capture's own run
    assert set(result.exact + result.numeric) == {"out/stable.txt", "out/stable.json"}


def test_cli_record_prints_the_summary_and_the_equivalent_command(lab, capsys):
    code = cli.main(["record", "--name", "exp", "--", sys.executable, "run.py"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "inputs found:   2" in out and "outputs, exact: 1" in out
    assert "Equivalent command" in out and "sciproof capture --name exp" in out
    manifest = json.loads((lab.path / "exp.sciproof.json").read_text())
    assert manifest["command"]["argv"][1:] == ["run.py"]


def test_cli_record_without_a_command_is_an_error(lab, capsys):
    assert cli.main(["record", "--name", "exp"]) == cli.EXIT_ERROR
    assert "no command given" in capsys.readouterr().err


def test_cli_record_prints_the_stderr_of_the_failed_run(lab, monkeypatch, capsys):
    monkeypatch.setenv("FAIL_SECOND", "1")
    code = cli.main(["record", "--name", "exp", "--", sys.executable, "run.py"])
    err = capsys.readouterr().err.splitlines()
    assert code == cli.EXIT_ERROR
    assert err[0].startswith("error: the second run of the command failed (exit status 1)")
    assert err[1] == "  last lines of its standard error:"
    assert err[-2].startswith("    FileNotFoundError:") and "gone.txt" in err[-2]


@pytest.mark.parametrize(
    "name, argv, message",
    [
        ("bad name with spaces!", [sys.executable, "run.py"], "invalid name"),
        ("exp", [], "no command given"),
        ("exp", [""], "no command given"),
    ],
)
def test_invalid_record_arguments_do_not_run_or_write_evidence(lab, name, argv, message):
    with pytest.raises(CaptureError, match=message):
        record(name=name, argv=argv, base=lab.path)
    assert lab.runs() == 0
    assert not list(lab.path.glob("*.sciproof*"))


def test_snapshot_limit_allows_the_boundary_and_refuses_the_next_file(tmp_path, monkeypatch):
    module = importlib.import_module("sciproof.record")
    monkeypatch.setattr(module, "MAX_SCANNED_FILES", 2)
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text(name)
    assert set(module._snapshot(tmp_path)) == {"a.txt", "b.txt"}
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "c.txt").write_text("third")
    with pytest.raises(CaptureError, match="more than 2 files.*smaller directory"):
        module._snapshot(tmp_path)


def test_record_scan_limit_stops_before_the_probe_runs(lab, monkeypatch):
    module = importlib.import_module("sciproof.record")
    monkeypatch.setattr(module, "MAX_SCANNED_FILES", 2)
    with pytest.raises(CaptureError, match="more than 2 files"):
        lab.go()
    assert lab.runs() == 0
    assert not list(lab.path.glob("*.sciproof*"))


def test_unparseable_numeric_output_is_captured_exactly(lab):
    script = lab.path / "run.py"
    script.write_text(
        script.read_text() + '\npathlib.Path("out/bad.json").write_text("not JSON")\n'
    )
    result = lab.go()
    assert "out/bad.json" in result.exact
    assert "out/bad.json" not in result.numeric
    assert verify_manifest(result.capture.manifest_path).status == Status.PASS


@pytest.mark.parametrize(
    "failure, message",
    [
        (CommandInterrupted("cancelled"), "interrupted; nothing was recorded"),
        (CommandTimeout("timeout after 1s"), "timeout after 1s; nothing was recorded"),
        (CommandNotRunnable("cannot launch command"), "cannot launch command"),
    ],
)
def test_second_run_failures_are_clean_capture_errors(lab, monkeypatch, failure, message):
    module = importlib.import_module("sciproof.record")

    def fail_run(argv, base, timeout):
        assert argv == [sys.executable, "run.py"]
        assert base == lab.path
        assert timeout == 5
        raise failure

    monkeypatch.setattr(module, "run_command", fail_run)
    with pytest.raises(CaptureError, match=message) as caught:
        lab.go(timeout=5)
    assert caught.value.__cause__ is failure
    assert lab.runs() == 1  # the real discovery run completed before the failing second run
    assert not (lab.path / "exp.sciproof.json").exists()
    assert not (lab.path / "exp.sciproof.d").exists()

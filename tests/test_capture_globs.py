"""Tests for dynamic glob expansion in capture command."""

from __future__ import annotations

import sys

import pytest

from sciproof.capture import CaptureError, capture
from sciproof.errors import UnsafePathError
from sciproof.paths import expand_glob, has_glob_magic


def _require_symlinks(tmp_path) -> None:
    try:
        probe = tmp_path / "probe_symlink"
        probe.symlink_to(tmp_path)
        probe.unlink()
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")


def test_glob_magic_detection():
    assert has_glob_magic("data/*.csv")
    assert has_glob_magic("data/split_?.json")
    assert has_glob_magic("data/split_[0-9].json")
    assert not has_glob_magic("data/regular.csv")


def test_expand_glob_matches(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "c.csv").write_text("c")

    matches = expand_glob(tmp_path, "*.txt")
    assert matches == ["a.txt", "b.txt"]


def test_expand_glob_drops_directories(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "a.txt").write_text("a")
    (tmp_path / "out" / "sub").mkdir()
    (tmp_path / "out" / "sub" / "b.txt").write_text("b")

    assert expand_glob(tmp_path, "out/**/*") == ["out/a.txt", "out/sub/b.txt"]


def test_expand_glob_excludes_sciproof_artifacts(tmp_path):
    (tmp_path / "run.sciproof.json").write_text("{}")
    (tmp_path / "run.sig").write_text("signature")

    (tmp_path / "run.sciproof.d").mkdir()
    (tmp_path / "run.sciproof.d" / "snapshot.json").write_text("{}")

    (tmp_path / "run.sciproof.d.tmp").mkdir()
    (tmp_path / "run.sciproof.d.tmp" / "staging.json").write_text("{}")

    (tmp_path / ".sciproof-rerun-test").mkdir()
    (tmp_path / ".sciproof-rerun-test" / "output.json").write_text("{}")

    (tmp_path / "results.json").write_text("{}")

    assert expand_glob(tmp_path, "**/*") == ["results.json"]


def test_expand_glob_records_file_symlink_by_its_own_path(tmp_path):
    _require_symlinks(tmp_path)
    (tmp_path / "real.txt").write_text("real")
    (tmp_path / "link.txt").symlink_to("real.txt")

    assert expand_glob(tmp_path, "*.txt") == ["link.txt", "real.txt"]


def test_expand_glob_does_not_follow_directory_symlinks(tmp_path):
    _require_symlinks(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.txt").write_text("inner")
    (tmp_path / "linkdir").symlink_to("sub", target_is_directory=True)

    assert expand_glob(tmp_path, "*") == []
    assert expand_glob(tmp_path, "linkdir/*.txt") == []


def test_expand_glob_drops_outside_and_broken_symlinks(tmp_path):
    _require_symlinks(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside")
    (tmp_path / "link_out.txt").symlink_to(outside)
    (tmp_path / "link_broken.txt").symlink_to("missing.txt")

    assert expand_glob(tmp_path, "*.txt") == []


def test_capture_with_input_glob(project):
    (project.root / "split_1.json").write_text('{"a": 1}')
    (project.root / "split_2.json").write_text('{"b": 2}')

    script = "import sys; sys.exit(0)"
    res = capture(
        name="glob_in",
        argv=[sys.executable, "-c", script],
        base=project.root,
        inputs=["split_*.json"],
        outputs=[],
        numeric_outputs=[],
    )
    assert res.exit_code == 0
    inp_paths = [inp["path"] for inp in res.manifest["inputs"]]
    assert "split_1.json" in inp_paths
    assert "split_2.json" in inp_paths


def test_capture_input_glob_ignores_unrelated_manifest(project):
    (project.root / "split_1.json").write_text('{"a": 1}')
    (project.root / "unrelated.sciproof.json").write_text("{}")

    res = capture(
        name="glob_manifest_input",
        argv=[sys.executable, "-c", "import sys; sys.exit(0)"],
        base=project.root,
        inputs=["*.json"],
        outputs=[],
        numeric_outputs=[],
    )

    inp_paths = [inp["path"] for inp in res.manifest["inputs"]]
    assert "split_1.json" in inp_paths
    assert "data.json" in inp_paths
    assert "unrelated.sciproof.json" not in inp_paths


def test_capture_with_unmatched_input_glob_fails(project):
    with pytest.raises(CaptureError, match="no files matched input glob"):
        capture(
            name="glob_fail",
            argv=[sys.executable, "-c", "import sys; sys.exit(0)"],
            base=project.root,
            inputs=["nonexistent_*.json"],
            outputs=[],
            numeric_outputs=[],
        )


def test_capture_with_unmatched_output_glob_fails_and_writes_no_manifest(project):
    with pytest.raises(CaptureError, match="no files matched output glob"):
        capture(
            name="glob_out_fail",
            argv=[sys.executable, "-c", "import sys; sys.exit(0)"],
            base=project.root,
            inputs=[],
            outputs=["out_glob/*.txt"],
            numeric_outputs=[],
        )
    assert not (project.root / "glob_out_fail.sciproof.json").exists()


def test_capture_with_unmatched_output_glob_fails_even_when_command_fails(project):
    # A failed command still records explicit missing outputs, but an output
    # glob that matched nothing has no file to record, so it must not be
    # silently dropped into a manifest with no declared outputs.
    with pytest.raises(CaptureError, match="no files matched output glob"):
        capture(
            name="glob_out_fail_cmd",
            argv=[sys.executable, "-c", "import sys; sys.exit(1)"],
            base=project.root,
            inputs=[],
            outputs=["out_glob/*.txt"],
            numeric_outputs=[],
        )
    assert not (project.root / "glob_out_fail_cmd.sciproof.json").exists()


def test_capture_with_unmatched_numeric_output_glob_fails(project):
    with pytest.raises(CaptureError, match="no files matched output-numeric glob"):
        capture(
            name="glob_num_fail",
            argv=[sys.executable, "-c", "import sys; sys.exit(0)"],
            base=project.root,
            inputs=[],
            outputs=[],
            numeric_outputs=["out_num/*.json"],
        )
    assert not (project.root / "glob_num_fail.sciproof.json").exists()


def test_capture_with_output_glob(project):
    script = (
        "import pathlib\n"
        "d = pathlib.Path('out_glob')\n"
        "d.mkdir(exist_ok=True)\n"
        "(d / 'res1.txt').write_text('result 1')\n"
        "(d / 'res2.txt').write_text('result 2')\n"
    )
    res = capture(
        name="glob_out",
        argv=[sys.executable, "-c", script],
        base=project.root,
        inputs=[],
        outputs=["out_glob/*.txt"],
        numeric_outputs=[],
    )
    assert res.exit_code == 0
    out_paths = [out["path"] for out in res.manifest["outputs"]]
    assert "out_glob/res1.txt" in out_paths
    assert "out_glob/res2.txt" in out_paths


def test_capture_force_output_glob_ignores_own_manifest(project):
    script = "import pathlib\npathlib.Path('result.json').write_text('{}')\n"
    argv = [sys.executable, "-c", script]

    first = capture(
        name="glob_force",
        argv=argv,
        base=project.root,
        inputs=[],
        outputs=["*.json"],
        numeric_outputs=[],
    )

    second = capture(
        name="glob_force",
        argv=argv,
        base=project.root,
        inputs=[],
        outputs=["*.json"],
        numeric_outputs=[],
        force=True,
    )

    assert "result.json" in [o["path"] for o in first.manifest["outputs"]]
    assert "result.json" in [o["path"] for o in second.manifest["outputs"]]
    assert "glob_force.sciproof.json" not in [o["path"] for o in second.manifest["outputs"]]


@pytest.mark.parametrize("pattern", ["/etc/*.conf", "/root/*"])
def test_expand_glob_absolute_pattern_raises(tmp_path, pattern):
    with pytest.raises(UnsafePathError, match="is absolute"):
        expand_glob(tmp_path, pattern)


@pytest.mark.parametrize("pattern", ["../sibling/*.csv", "a/../b.txt"])
def test_expand_glob_dotdot_pattern_raises(tmp_path, pattern):
    with pytest.raises(UnsafePathError, match="'\\.\\.' components"):
        expand_glob(tmp_path, pattern)


def test_expand_glob_empty_segment_raises(tmp_path):
    with pytest.raises(UnsafePathError):
        expand_glob(tmp_path, "dir//*.txt")


def test_output_glob_overlapping_input_raises_clean_capture_error(project):
    data_dir = project.root / "data"
    data_dir.mkdir(exist_ok=True)
    in_file = data_dir / "in.csv"
    in_file.write_text("1,2\n")

    script = (
        "import pathlib\n"
        "d = pathlib.Path('data')\n"
        "d.mkdir(exist_ok=True)\n"
        "(d / 'out.csv').write_text('3,4\\n')\n"
    )

    with pytest.raises(CaptureError, match="a file cannot be both input and output: data/in.csv"):
        capture(
            name="overlap_test",
            argv=[sys.executable, "-c", script],
            base=project.root,
            inputs=["data/in.csv"],
            outputs=["data/*.csv"],
            numeric_outputs=[],
        )

    assert not (project.root / "overlap_test.sciproof.json").exists()


def test_tolerance_on_glob_matched_numeric_output(project):
    script = (
        "import pathlib, json\n"
        "d = pathlib.Path('out_tol')\n"
        "d.mkdir(exist_ok=True)\n"
        "with open(d / 'loss.json', 'w') as f:\n"
        "    json.dump({'l': 1.0}, f)\n"
    )

    res = capture(
        name="tol_glob_test",
        argv=[sys.executable, "-c", script],
        base=project.root,
        inputs=[],
        outputs=[],
        numeric_outputs=["out_tol/*.json"],
        tolerances={"out_tol/loss.json": {"rtol": 1e-3, "atol": 1e-4}},
    )

    assert res.exit_code == 0
    record = next(o for o in res.manifest["outputs"] if o["path"] == "out_tol/loss.json")
    assert record["compare"]["rtol"] == 1e-3
    assert record["compare"]["atol"] == 1e-4


def test_claim_on_glob_matched_output(project):
    script = (
        "import pathlib, json\n"
        "d = pathlib.Path('out_claim')\n"
        "d.mkdir(exist_ok=True)\n"
        "with open(d / 'metrics.json', 'w') as f:\n"
        "    json.dump({'acc': 0.95}, f)\n"
    )

    res = capture(
        name="claim_glob_test",
        argv=[sys.executable, "-c", script],
        base=project.root,
        inputs=[],
        outputs=[],
        numeric_outputs=["out_claim/*.json"],
        claims=[("out_claim/metrics.json", "$.acc >= 0.9")],
    )

    assert res.exit_code == 0
    assert len(res.manifest["claims"]) == 1
    assert res.manifest["claims"][0]["output"] == "out_claim/metrics.json"
    assert res.manifest["claims"][0]["location"] == "$.acc"


def test_tolerance_on_glob_pattern_file_not_produced_fails(project):
    script = (
        "import pathlib, json\n"
        "d = pathlib.Path('out_unproduced')\n"
        "d.mkdir(exist_ok=True)\n"
        "with open(d / 'other.json', 'w') as f:\n"
        "    json.dump({'x': 1}, f)\n"
    )

    with pytest.raises(CaptureError, match="not matched by any --output-numeric declaration"):
        capture(
            name="unproduced_tol_test",
            argv=[sys.executable, "-c", script],
            base=project.root,
            inputs=[],
            outputs=[],
            numeric_outputs=["out_unproduced/*.json"],
            tolerances={"out_unproduced/missing.json": {"rtol": 1e-3}},
        )

    assert not (project.root / "unproduced_tol_test.sciproof.json").exists()

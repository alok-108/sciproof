"""Tests for dynamic glob expansion in capture command."""

from __future__ import annotations

import sys

import pytest

from sciproof.capture import CaptureError, capture
from sciproof.errors import UnsafePathError
from sciproof.paths import expand_glob, has_glob_magic


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


def test_expand_glob_records_file_symlink_by_its_own_path(tmp_path):
    (tmp_path / "real.txt").write_text("real")
    (tmp_path / "link.txt").symlink_to("real.txt")

    assert expand_glob(tmp_path, "*.txt") == ["link.txt", "real.txt"]


def test_expand_glob_does_not_follow_directory_symlinks(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.txt").write_text("inner")
    (tmp_path / "linkdir").symlink_to("sub", target_is_directory=True)

    assert expand_glob(tmp_path, "*") == []
    assert expand_glob(tmp_path, "linkdir/*.txt") == []


def test_expand_glob_drops_outside_and_broken_symlinks(tmp_path):
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

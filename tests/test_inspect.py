"""``sciproof inspect``: the readable summary covers every recorded manifest shape."""

from __future__ import annotations

import json

from sciproof.claims import parse_claim
from sciproof.inspect_manifest import describe, inspect_file


def _captured(project):
    project.capture()
    return json.loads(project.manifest.read_text())


def test_recorded_environment_variables_are_listed(project):
    manifest = _captured(project)
    manifest["environment"]["env_vars"] = {
        "CUDA_VISIBLE_DEVICES": "0",
        "OMP_NUM_THREADS": "4",
    }
    assert "variables:   CUDA_VISIBLE_DEVICES=0, OMP_NUM_THREADS=4" in describe(manifest)


def test_recorded_lockfiles_are_listed(project):
    manifest = _captured(project)
    manifest["environment"]["lockfiles"] = [
        {"path": "requirements.txt", "sha256": "3a1b2c3d4e5f67890123456789abcdef"},
        {"path": "uv.lock", "sha256": "8c4d5e6f7a8b9c0d123456789abcdef0"},
    ]
    assert "lockfiles:   requirements.txt (3a1b2c3d...), uv.lock (8c4d5e6f...)" in describe(
        manifest
    )


def test_a_manifest_without_git_metadata_says_not_recorded(project):
    manifest = _captured(project)
    manifest["git"] = None
    assert "git:         not recorded" in describe(manifest)


def test_a_manifest_without_outputs_says_none(project):
    manifest = _captured(project)
    manifest["outputs"] = []
    assert "Outputs (0):\n  (none)" in describe(manifest)


def test_recorded_hardware_and_gpus_are_listed(project):
    manifest = _captured(project)
    manifest["environment"]["hardware"] = {
        "cpu_count": 8,
        "processor": "Test CPU",
        "gpu_probe": "nvidia-smi",
        "gpus": [{"name": "NVIDIA Test GPU"}, {"name": "NVIDIA Second GPU"}],
    }
    assert "hardware:    8 CPUs, Test CPU; GPUs: NVIDIA Test GPU, NVIDIA Second GPU" in describe(
        manifest
    )


def test_empty_stream_preview_is_displayed(project):
    manifest = _captured(project)
    manifest["execution"]["stdout"]["text"] = ""
    manifest["execution"]["stdout"]["truncated"] = False
    assert "      (empty)" in describe(manifest)


def test_long_stream_preview_is_limited_to_eight_lines(project):
    manifest = _captured(project)
    manifest["execution"]["stdout"]["text"] = "\n".join(f"line {number}" for number in range(1, 11))
    manifest["execution"]["stdout"]["truncated"] = False
    text = describe(manifest)
    assert "      | line 1" in text
    assert "      | line 8" in text
    assert "      | line 9" not in text
    assert "      | ..." in text


def test_directory_output_shows_file_count(project):
    manifest = _captured(project)
    manifest["outputs"][0]["path"] = "out/results"
    manifest["outputs"][0]["kind"] = "directory"
    manifest["outputs"][0]["files"] = ["a.txt", "b.txt"]
    assert "out/results  directory, 2 files, tree " in describe(manifest)


def test_absent_output_is_displayed(project):
    manifest = _captured(project)
    manifest["outputs"][0]["present"] = False
    assert "(absent when captured)" in describe(manifest)


def test_numeric_output_comparison_settings_are_displayed(project):
    manifest = _captured(project)
    assert "numeric (rtol=1e-09, atol=0.0)" in describe(manifest)


def test_multiline_note_is_displayed(project):
    manifest = _captured(project)
    manifest["note"] = "First line\nSecond line\nThird line"
    text = describe(manifest)
    assert "Note:" in text
    assert "  First line" in text
    assert "  Second line" in text
    assert "  Third line" in text


def test_note_is_limited_to_ten_lines(project):
    manifest = _captured(project)
    manifest["note"] = "\n".join(f"Note {i}" for i in range(1, 13))
    text = describe(manifest)
    assert "Note 10" in text
    assert "Note 11" not in text


def test_inspect_file_reads_manifest_from_path(project):
    project.capture()
    text = inspect_file(project.manifest)
    assert "SciProof manifest: exp" in text
    assert "Execution:" in text


def test_multiple_claims_are_displayed_in_readable_form(project):
    manifest = _captured(project)
    manifest["claims"] = [
        parse_claim("out/result.json", "$.value >= 42"),
        parse_claim("out/result.json", "$.value ~= 42 atol=0.5"),
    ]

    text = describe(manifest)

    assert "Claims (2), checked by verify:" in text
    assert "out/result.json: $.value >= 42" in text
    assert "out/result.json: $.value ~= 42 (rtol=0.0, atol=0.5)" in text

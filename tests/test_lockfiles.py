"""Root dependency pins are captured as metadata and compared without reading them again."""

import errno
import hashlib
import json

import pytest

from sciproof import environment, load_manifest
from sciproof.diffmanifests import diff_manifests
from sciproof.errors import CaptureError, ManifestError

NAMES = ("environment.yml", "poetry.lock", "requirements.txt", "uv.lock")


def test_record_discovers_all_root_lockfiles_and_hashes_exact_bytes(project):
    content = b"pins\r\n\x00\xff"
    for name in NAMES:
        (project.root / name).write_bytes(content)
    (project.root / "nested").mkdir()
    (project.root / "nested" / "uv.lock").write_text("not discovered")
    result = project.capture()
    assert load_manifest(result.manifest_path)["environment"]["lockfiles"] == [
        {"path": name, "sha256": hashlib.sha256(content).hexdigest()} for name in NAMES
    ]


def test_record_without_lockfiles_keeps_metadata_empty(project):
    (project.root / "uv.lock").mkdir()
    assert load_manifest(project.capture().manifest_path)["environment"]["lockfiles"] == []


def test_lockfile_hash_is_taken_before_the_command_runs(project):
    path = project.root / "uv.lock"
    path.write_bytes(b"before")
    script = project.root / "experiment.py"
    script.write_text(
        script.read_text().replace(
            "sys.exit(", 'pathlib.Path("uv.lock").write_text("after")\nsys.exit('
        )
    )
    manifest = load_manifest(project.capture().manifest_path)
    assert manifest["environment"]["lockfiles"] == [
        {"path": "uv.lock", "sha256": hashlib.sha256(b"before").hexdigest()}
    ]
    assert path.read_text() == "after"


def test_outside_symlink_is_refused_before_execution(project, tmp_path):
    outside = tmp_path / "outside.lock"
    outside.write_text("private")
    try:
        (project.root / "uv.lock").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(CaptureError, match="outside"):
        project.capture()
    assert project.runs() == 0


def test_diff_reports_added_changed_and_removed_lockfiles(project):
    (project.root / "uv.lock").write_text("old")
    (project.root / "poetry.lock").write_text("gone")
    old = project.capture(name="old").manifest_path
    (project.root / "uv.lock").write_text("new")
    (project.root / "poetry.lock").unlink()
    (project.root / "requirements.txt").write_text("added")
    new = project.capture(name="new").manifest_path
    # Diff uses only the recorded evidence, even after the files disappear.
    (project.root / "uv.lock").unlink()
    changes = [c for c in diff_manifests(old, new).changes if c.subject.startswith("lockfile ")]
    assert [(c.section, c.kind, c.subject) for c in changes] == [
        ("environment", "removed", "lockfile poetry.lock"),
        ("environment", "added", "lockfile requirements.txt"),
        ("environment", "changed", "lockfile uv.lock"),
    ]
    assert hashlib.sha256(b"old").hexdigest() in changes[2].message
    assert hashlib.sha256(b"new").hexdigest() in changes[2].message


def test_lockfile_symlink_loop_is_a_clean_capture_error(project):
    try:
        (project.root / "uv.lock").symlink_to("uv.lock")
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(CaptureError, match="cannot record lockfile"):
        project.capture()
    assert project.runs() == 0


def test_unreadable_lockfile_is_a_clean_capture_error(tmp_path, monkeypatch):
    path = tmp_path / "uv.lock"
    path.write_text("pins")

    def refuse_hash(_path):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(environment, "sha256_file", refuse_hash)
    with pytest.raises(CaptureError, match="cannot record lockfile 'uv.lock':.*Permission denied"):
        environment.lockfile_hashes(tmp_path)


def test_diff_accepts_legacy_manifest_without_lockfiles(project):
    old = project.capture(name="old").manifest_path
    data = json.loads(old.read_text())
    data["environment"].pop("lockfiles", None)
    old.write_text(json.dumps(data))
    new = project.capture(name="new").manifest_path
    assert diff_manifests(old, new).identical


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        [None],
        [{"path": "../secret", "sha256": "a" * 64}],
        [{"path": "uv.lock", "sha256": "invalid"}],
        [{"path": "uv.lock", "sha256": "a" * 64}] * 2,
    ],
)
def test_invalid_lockfile_metadata_is_refused(project, value):
    path = project.capture().manifest_path
    data = json.loads(path.read_text())
    data["environment"]["lockfiles"] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ManifestError, match="lockfiles"):
        load_manifest(path)

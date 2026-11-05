"""Tests for container sandboxing and hermetic execution wrapping."""

from __future__ import annotations

from pathlib import Path

from sciproof.runner import build_container_command


def test_build_container_command_docker(tmp_path: Path):
    cmd = build_container_command(["python", "train.py"], tmp_path, "docker://python:3.12-slim")
    assert cmd[0] == "docker"
    assert cmd[1:3] == ["run", "--rm"]
    assert cmd[3] == "-v"
    assert "/workspace" in cmd[4]
    assert cmd[5:7] == ["-w", "/workspace"]
    assert cmd[7] == "python:3.12-slim"
    assert cmd[8:] == ["python", "train.py"]


def test_build_container_command_podman(tmp_path: Path):
    cmd = build_container_command(["bash", "eval.sh"], tmp_path, "podman://ubuntu:22.04")
    assert cmd[0] == "podman"
    assert cmd[7] == "ubuntu:22.04"
    assert cmd[8:] == ["bash", "eval.sh"]


def test_build_container_command_apptainer_sif(tmp_path: Path):
    sif_file = tmp_path / "env.sif"
    cmd = build_container_command(["python", "-m", "pytest"], tmp_path, str(sif_file))
    assert cmd[0] == "apptainer"
    assert cmd[1] == "exec"
    assert cmd[2] == "--bind"
    assert cmd[4:6] == ["--pwd", "/workspace"]
    assert cmd[6] == str(sif_file)
    assert cmd[7:] == ["python", "-m", "pytest"]


def test_build_container_command_singularity_url(tmp_path: Path):
    cmd = build_container_command(["ls", "-la"], tmp_path, "singularity://image.sif")
    assert cmd[0] == "singularity"
    assert cmd[1] == "exec"
    assert cmd[6] == "image.sif"

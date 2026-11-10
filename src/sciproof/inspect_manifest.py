"""Readable summary of a manifest. This module never executes anything."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sciproof.claims import claim_text
from sciproof.manifest import load_manifest
from sciproof.textutil import safe_text

_PREVIEW_LINES = 8


def _kind(item: dict[str, Any]) -> str:
    """Words that tell a directory (and how many files it holds) apart from a file."""
    if item.get("kind") == "directory":
        return f"directory, {len(item.get('files') or [])} files, tree "
    return ""


def _stream_preview(stream: dict[str, Any]) -> list[str]:
    text = stream["text"]
    if not text.strip():
        return ["      (empty)"]
    lines = text.splitlines()
    shown = [f"      | {safe_text(line, 200)}" for line in lines[:_PREVIEW_LINES]]
    if len(lines) > _PREVIEW_LINES or stream["truncated"]:
        shown.append("      | ...")
    return shown


def describe(manifest: dict[str, Any]) -> str:
    env = manifest["environment"]
    execution = manifest["execution"]
    git = manifest.get("git")
    lines = [
        f"SciProof manifest: {safe_text(manifest['name'])}",
        f"  format:      {manifest['schema']} {manifest['schema_version']}",
        f"  captured:    {safe_text(manifest['created_at'])}",
        "",
        "Command (recorded, NOT executed by 'inspect'):",
        "  " + " ".join(safe_text(part, 200) for part in manifest["command"]["argv"]),
        "  working directory: the directory containing the manifest",
        "",
        "Environment:",
        f"  python:      {safe_text(env['python']['version'])} "
        f"({safe_text(env['python'].get('implementation', '?'))})",
        f"  platform:    {safe_text(env['platform'].get('platform', '?'))}",
        f"  recorded by: {safe_text(env.get('source', 'unknown'))}",
        f"  packages:    {len(manifest['dependencies'])} recorded",
    ]
    if env["env_vars"]:
        lines.append(
            "  variables:   "
            + ", ".join(
                f"{safe_text(k)}={safe_text(v, 80)}" for k, v in sorted(env["env_vars"].items())
            )
        )
    lockfiles = env.get("lockfiles") or []
    if lockfiles:
        lines.append(
            "  lockfiles:   "
            + ", ".join(
                f"{safe_text(lf['path'])} ({safe_text(lf['sha256'])[:8]}...)" for lf in lockfiles
            )
        )
    if git:
        state = "UNCOMMITTED CHANGES" if git["dirty"] else "clean"
        lines.append(f"  git:         {safe_text(git['commit'])[:12]} ({state})")
    else:
        lines.append("  git:         not recorded")

    hardware = env.get("hardware")
    if hardware:
        gpus = ", ".join(safe_text(g["name"], 60) for g in hardware["gpus"]) or (
            "none found" if hardware["gpu_probe"] == "nvidia-smi" else "not probed (no nvidia-smi)"
        )
        processor = safe_text(hardware["processor"], 60)
        lines.append(f"  hardware:    {hardware['cpu_count']} CPUs, {processor}; GPUs: {gpus}")
    if manifest.get("note"):
        lines += ["", "Note:"]
        lines += [f"  {safe_text(line, 300)}" for line in manifest["note"].splitlines()[:10]]

    lines += ["", f"Inputs ({len(manifest['inputs'])}):"]
    lines += [
        f"  {safe_text(i['path'])}  {_kind(i)}sha256:{i['sha256'][:16]}...  {i['size']} bytes"
        for i in manifest["inputs"]
    ] or ["  (none)"]

    lines += ["", "Execution:"]
    lines.append(f"  exit code:   {execution['exit_code']}")
    lines.append(f"  duration:    {execution['duration_seconds']} s")
    lines.append(f"  stdout:      {execution['stdout']['size']} bytes")
    lines += _stream_preview(execution["stdout"])
    lines.append(f"  stderr:      {execution['stderr']['size']} bytes")
    lines += _stream_preview(execution["stderr"])

    lines += ["", f"Outputs ({len(manifest['outputs'])}):"]
    for item in manifest["outputs"]:
        compare = item["compare"]
        if compare["mode"] == "numeric":
            how = f"numeric (rtol={compare['rtol']}, atol={compare['atol']})"
        else:
            how = "exact hash"
        if item["present"]:
            lines.append(
                f"  {safe_text(item['path'])}  {_kind(item)}sha256:{item['sha256'][:16]}...  "
                f"{item['size']} bytes  compare: {how}"
            )
        else:
            lines.append(f"  {safe_text(item['path'])}  (absent when captured)  compare: {how}")
    if not manifest["outputs"]:
        lines.append("  (none)")

    claims = manifest.get("claims") or []
    if claims:
        lines += ["", f"Claims ({len(claims)}), checked by verify:"]
        lines += [f"  {safe_text(claim_text(c), 300)}" for c in claims]

    lines += [
        "",
        "Next: 'sciproof verify <manifest>' checks that the files still match this evidence.",
        "      Add --rerun only if you trust the command shown above.",
    ]
    return "\n".join(lines)


def inspect_file(path: Path) -> str:
    return describe(load_manifest(path))

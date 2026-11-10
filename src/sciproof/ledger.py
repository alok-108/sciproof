"""List the manifests in a directory, and optionally check all of them at once.

There is no database and no index: a manifest is a file, so the "ledger" is the directory. The
listing reads each manifest (validated like any other) and, with ``check``, runs the integrity
verification of ``verify`` (never a rerun) on it. Nothing is executed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sciproof.errors import SciProofError, UsageError
from sciproof.manifest import MANIFEST_SUFFIX, load_manifest
from sciproof.markdown import escape
from sciproof.signing import signature_path
from sciproof.textutil import safe_text
from sciproof.verify import verify

MAX_MANIFESTS = 1000
LIST_SCHEMA = "sciproof.list"
LIST_SCHEMA_VERSION = "1.0"
_SKIPPED_DIRECTORIES = {"node_modules", "__pycache__"}


@dataclass
class Entry:
    file: str  # path relative to the listed directory, '/'-separated
    name: str | None = None
    created_at: str | None = None
    exit_code: int | None = None
    commit: str | None = None
    dirty: bool | None = None
    inputs: int = 0
    outputs: int = 0
    claims: int = 0
    note: str | None = None
    signed: bool = False
    verdict: str | None = None  # PASS, FAIL or INCONCLUSIVE, only with check=True
    error: str | None = None  # why the manifest could not be read

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _find(directory: Path, recursive: bool) -> list[Path]:
    found: list[Path] = []
    if not recursive:
        candidates = sorted(directory.glob(f"*{MANIFEST_SUFFIX}"))
        return [p for p in candidates if p.is_file() and not p.is_symlink()][: MAX_MANIFESTS + 1]
    for current, subdirectories, files in os.walk(directory, followlinks=False):
        subdirectories[:] = sorted(
            d for d in subdirectories if not d.startswith(".") and d not in _SKIPPED_DIRECTORIES
        )
        for file in sorted(files):
            path = Path(current) / file
            if file.endswith(MANIFEST_SUFFIX) and not path.is_symlink():
                found.append(path)
                if len(found) > MAX_MANIFESTS:
                    return found
    return found


def list_manifests(
    directory: Path | str, *, recursive: bool = False, check: bool = False
) -> list[Entry]:
    """Describe every ``*.sciproof.json`` below ``directory``, newest capture first."""
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise UsageError(f"not a directory: {directory}")
    paths = _find(directory, recursive)
    if len(paths) > MAX_MANIFESTS:
        raise UsageError(f"more than {MAX_MANIFESTS} manifests; list a smaller directory")
    entries: list[Entry] = []
    for path in paths:
        entry = Entry(path.relative_to(directory).as_posix())
        try:
            manifest = load_manifest(path)
        except SciProofError as exc:
            entry.error = safe_text(str(exc), 300)
            entries.append(entry)
            continue
        git = manifest.get("git")
        entry.name = manifest["name"]
        entry.created_at = manifest["created_at"]
        entry.exit_code = manifest["execution"]["exit_code"]
        entry.commit = git["commit"][:12] if git else None
        entry.dirty = git["dirty"] if git else None
        entry.inputs = len(manifest["inputs"])
        entry.outputs = len(manifest["outputs"])
        entry.claims = len(manifest.get("claims") or [])
        entry.note = manifest.get("note")
        entry.signed = signature_path(path).is_file()
        if check:
            try:
                entry.verdict = verify(path).status.value
            except SciProofError as exc:  # pragma: no cover - load_manifest succeeded above
                entry.error = safe_text(str(exc), 300)
        entries.append(entry)
    entries.sort(key=lambda e: (e.created_at or "", e.file), reverse=True)  # invalid ones last
    return entries


def worst_verdict(entries: list[Entry]) -> str | None:
    """``FAIL`` if any manifest failed or is invalid, else ``INCONCLUSIVE``, else ``PASS``."""
    if any(e.error or e.verdict == "FAIL" for e in entries):
        return "FAIL"
    if any(e.verdict == "INCONCLUSIVE" for e in entries):
        return "INCONCLUSIVE"
    if any(e.verdict is not None for e in entries):
        return "PASS"
    return None


def summary(entries: list[Entry], checked: bool) -> str:
    text = f"{len(entries)} manifest(s)"
    if checked:
        counts = {
            v: sum(1 for e in entries if e.verdict == v) for v in ("PASS", "FAIL", "INCONCLUSIVE")
        }
        invalid = sum(1 for e in entries if e.error)
        text += ": " + ", ".join(f"{n} {v}" for v, n in counts.items() if n)
        if invalid:
            text += f", {invalid} invalid"
    return text


def format_text(entries: list[Entry], checked: bool) -> str:
    if not entries:
        return "no manifests found"
    lines = []
    for e in entries:
        if e.error:
            lines.append(f"  {safe_text(e.file)}  INVALID: {e.error}")
            continue
        state = ""
        if e.commit:
            state = f"  git {e.commit}{'+dirty' if e.dirty else ''}"
        verdict = f"  {e.verdict}" if e.verdict else ""
        lines.append(
            f"  {safe_text(e.file)}  captured {safe_text(e.created_at or '')}  exit {e.exit_code}"
            f"  in {e.inputs}  out {e.outputs}  claims {e.claims}"
            f"{'  signed' if e.signed else ''}{state}{verdict}"
            f"{('  note: ' + safe_text(e.note, 40)) if e.note else ''}"
        )
    return "\n".join([*lines, "", f"summary: {summary(entries, checked)}"])


def format_json(entries: list[Entry], checked: bool) -> str:
    return json.dumps(
        {
            "schema": LIST_SCHEMA,
            "schema_version": LIST_SCHEMA_VERSION,
            "checked": checked,
            "summary": summary(entries, checked),
            "result": worst_verdict(entries),
            "manifests": [e.to_dict() for e in entries],
        },
        indent=2,
        allow_nan=False,
    )


def format_markdown(entries: list[Entry], checked: bool) -> str:
    lines = ["## SciProof manifests", "", f"> {escape(summary(entries, checked))}", ""]
    if entries:
        lines += [
            "| File | Name | Captured | Exit | Git | Signed | Result | Note |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for e in entries:
            if e.error:
                lines.append(f"| {escape(e.file)} | | | | | | INVALID: {escape(e.error)} | |")
                continue
            git = f"{e.commit}{' (dirty)' if e.dirty else ''}" if e.commit else "-"
            note = escape(e.note) if e.note else "-"
            signed = "yes" if e.signed else "no"
            verdict = escape(e.verdict or "-")
            lines.append(
                f"| {escape(e.file)} | {escape(e.name)} | {escape(e.created_at)} | {e.exit_code} "
                f"| {escape(git)} | {signed} | {verdict} | {note} |"
            )
    lines.append("")
    return "\n".join(lines)

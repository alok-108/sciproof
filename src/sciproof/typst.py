"""Numbers for a scientific paper written in Typst: ``sciproof export --format typst``.

Typst is a modern scientific typesetting system. This module writes the values of chosen
locations of verified outputs as Typst variables::

    // SciProof 0.4.1: values of baseline.sciproof.json (integrity verification: PASS)
    #let accuracy = 0.93
    #let mean_loss = 0.0412

so that the paper imports them via ``#import "evidence.typ": accuracy, mean_loss`` and numbers
can only come from the recorded, verified evidence.

Nothing is written unless the integrity verification of the manifest passes.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from sciproof import __version__
from sciproof.claims import location_matches_file
from sciproof.errors import SciProofError, UsageError
from sciproof.manifest import load_manifest
from sciproof.numeric import NumericFormatError, load_numeric
from sciproof.paths import resolve_inside, validate_relative_path
from sciproof.textutil import safe_text
from sciproof.verify import Status, verify

TYPST_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
MAX_VALUES = 200

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _typst_string(value: str) -> str:
    cleaned = _CONTROL.sub(" ", value)
    escaped = cleaned.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def format_typst_value(value: object) -> str:
    """Format a Python object as a Typst literal value."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "calc.nan"
        if math.isinf(value):
            return "calc.inf" if value > 0 else "-calc.inf"
        if value != 0 and not 1e-4 <= abs(value) < 1e6:
            return f"{value:.9e}"
        return f"{value:.10g}"
    return _typst_string(str(value))


def _auto_typst_name(location: str, output_path: str, existing: set[str]) -> str:
    raw = location.removeprefix("$.")
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", raw) if p]
    candidate = "_".join(part.lower() for part in parts)
    stem_parts = [p for p in re.split(r"[^A-Za-z0-9]+", Path(output_path).stem) if p]
    stem = "_".join(p.lower() for p in stem_parts)
    if not candidate or candidate[0].isdigit():
        candidate = f"{stem}_{candidate}" if stem else f"val_{candidate}"
    name = re.sub(r"[^A-Za-z0-9_]", "", candidate)[:35]
    if not name or name[0].isdigit():
        name = f"var_{name}"[:35]
    if not name:
        name = "metric"
    base_name = name
    counter = 1
    while name in existing or not TYPST_IDENTIFIER.fullmatch(name):
        name = f"{base_name[:30]}_{counter}"
        counter += 1
    existing.add(name)
    return name


def to_typst(manifest_path: Path | str, values: list[tuple[str, str, str]] | None = None) -> str:
    """Typst variable definitions for ``values`` (``(variable name, output file, location)``).

    If ``values`` is None, all numeric leaves in declared outputs are automatically exported.
    Raises ``SciProofError`` unless the integrity verification of the manifest is ``PASS``.
    """
    manifest_path = Path(manifest_path).resolve()
    manifest = load_manifest(manifest_path)
    base = manifest_path.parent
    cache: dict[str, dict[str, object]] = {}

    if values is not None and not values:
        raise UsageError("give at least one --value NAME OUTPUT LOCATION")

    if values is None:
        auto_values: list[tuple[str, str, str]] = []
        used_names: set[str] = set()
        for output_record in manifest.get("outputs", []):
            if output_record.get("kind") == "directory" or not output_record.get("present", True):
                continue
            out_path = output_record["path"]
            if Path(out_path).suffix.lower() not in (".json", ".csv", ".tsv"):
                continue
            try:
                leaves = dict(load_numeric(resolve_inside(base, out_path)))
                cache[out_path] = leaves
            except (NumericFormatError, SciProofError, OSError):
                continue
            for loc in leaves:
                if loc.startswith("$#"):
                    continue
                var_name = _auto_typst_name(loc, out_path, used_names)
                auto_values.append((var_name, out_path, loc))
                if len(auto_values) >= MAX_VALUES:
                    break
            if len(auto_values) >= MAX_VALUES:
                break
        if not auto_values:
            raise UsageError("no numeric outputs found in manifest to export as Typst variables")
        values = auto_values

    if len(values) > MAX_VALUES:
        raise UsageError(f"at most {MAX_VALUES} values per export")

    names = [name for name, _, _ in values]
    for name in names:
        if not TYPST_IDENTIFIER.fullmatch(name):
            raise UsageError(
                f"--value {safe_text(name, 60)!r}: a Typst variable name must be 1 to 40 "
                "alphanumeric/underscore characters and cannot start with a digit"
            )
    if len(set(names)) != len(names):
        raise UsageError("a variable name is used more than once")

    declared = {o["path"]: o for o in manifest["outputs"]}
    for _, output, location in values:
        validate_relative_path(output)
        item = declared.get(output)
        if item is None or item.get("kind") == "directory" or not item.get("present", True):
            raise UsageError(f"{output!r} is not a declared output file of this manifest")
        if not location_matches_file(location, output):
            raise UsageError(
                f"{location!r} is not a location in {output!r}; use $.key[0].name for JSON or "
                "r2c1 for CSV/TSV"
            )

    report = verify(manifest_path)
    if report.status is not Status.PASS:
        raise SciProofError(
            f"not exporting values: the verification of {manifest_path.name} is "
            f"{report.status.value} ({report.reason}). Run 'sciproof verify' to see why."
        )

    cache = {}
    lines = [
        f"// SciProof {__version__}: values of {_CONTROL.sub(' ', manifest_path.name)} "
        "(integrity verification: PASS)",
        "// Generated by 'sciproof export --format typst'; do not edit by hand.",
    ]
    for name, output, location in values:
        if output not in cache:
            cache[output] = dict(load_numeric(resolve_inside(base, output)))
        leaves = cache[output]
        if location not in leaves:
            raise SciProofError(f"{location} is not present in {output}")
        value: Any = leaves[location]
        lines.append(f"#let {name} = {format_typst_value(value)}")
    return "\n".join(lines) + "\n"

"""Numbers for a paper, taken from verified outputs: ``sciproof export --format latex``.

A number typed into a paper by hand can drift away from the experiment that produced it. This
module writes the values of chosen locations of the declared outputs as LaTeX macros::

    % SciProof 0.4.1: values of baseline.sciproof.json (verification: PASS)
    \\newcommand{\\accuracy}{0.93}

so that the paper says ``\\accuracy{}`` and the number can only come from the recorded output.

Nothing is written unless the integrity verification of the manifest passes: the file is read
only after its SHA-256 has been checked, and the named locations must exist in a declared output
of a supported type (``.json``, ``.csv``, ``.tsv``). Nothing is executed. A passing verification
shows that the files are what was recorded; it does not show that the number is correct.
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

MACRO_NAME = re.compile(r"[A-Za-z]{1,40}")
MAX_VALUES = 200
_LATEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
# TeX does not allow these in a macro body without trouble, and they do not belong in a number
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _text(value: str) -> str:
    cleaned = _CONTROL.sub(" ", value)
    return "".join(_LATEX_SPECIAL.get(char, char) for char in cleaned)


def format_value(value: object) -> str:
    """``value`` as the body of a LaTeX macro."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return r"$\infty$" if value > 0 else r"$-\infty$"
        if value != 0 and not 1e-4 <= abs(value) < 1e6:
            mantissa, _, exponent = f"{value:.9e}".partition("e")
            mantissa = mantissa.rstrip("0").rstrip(".")
            return rf"${mantissa}\times 10^{{{int(exponent)}}}$"
        return f"{value:.10g}"
    return _text(str(value))


def _read(base: Path, output: str, location: str, cache: dict[str, dict[str, object]]) -> object:
    if output not in cache:
        try:
            cache[output] = dict(load_numeric(resolve_inside(base, output)))
        except NumericFormatError as exc:
            raise SciProofError(f"{output}: {exc}") from exc
    leaves = cache[output]
    if location not in leaves:
        raise SciProofError(f"{location} is not present in {output}")
    return leaves[location]


def _int_to_letters(n: int) -> str:
    """Convert a positive integer to letters (1 -> A, 2 -> B, ..., 26 -> Z, 27 -> AA)."""
    result = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result.append(chr(ord("A") + rem))
    return "".join(reversed(result))


def _auto_macro_name(location: str, output_path: str, existing: set[str]) -> str:
    raw = location.removeprefix("$.")
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", raw) if p]
    candidate = "".join(part.capitalize() for part in parts)
    stem_parts = [p for p in re.split(r"[^A-Za-z0-9]+", Path(output_path).stem) if p]
    stem = "".join(p.capitalize() for p in stem_parts)
    if not candidate or candidate[0].isdigit():
        candidate = f"{stem}{candidate}"
    letters = re.sub(r"[^A-Za-z]", "", candidate)[:35]
    stem_letters = re.sub(r"[^A-Za-z]", "", stem)[:30]
    if not letters:
        letters = f"{stem_letters}Value"[:35] if stem_letters else "Metric"
    if not letters:
        letters = "Metric"
    name = letters
    if name not in existing and MACRO_NAME.fullmatch(name):
        existing.add(name)
        return name

    # Try qualifying with the output stem if not already prefixed
    if stem and not name.startswith(stem):
        stem_prefixed = re.sub(r"[^A-Za-z]", "", f"{stem}{name}")[:35]
        if stem_prefixed and stem_prefixed not in existing and MACRO_NAME.fullmatch(stem_prefixed):
            existing.add(stem_prefixed)
            return stem_prefixed
        base = stem_prefixed[:30] if stem_prefixed else name[:30]
    else:
        base = name[:30]

    # Disambiguate with letter suffixes (B, C, D... then AA, AB...); bound loop
    counter = 2
    for _ in range(5000):
        candidate_name = f"{base}{_int_to_letters(counter)}"
        if candidate_name not in existing and MACRO_NAME.fullmatch(candidate_name):
            existing.add(candidate_name)
            return candidate_name
        counter += 1

    raise SciProofError(
        f"unable to generate unique LaTeX macro name for {location} in {output_path}"
    )


def to_latex(manifest_path: Path | str, values: list[tuple[str, str, str]] | None = None) -> str:
    """``\\newcommand`` lines for ``values`` (``(macro name, output file, location)``).

    If ``values`` is None, all numeric leaves in declared outputs are automatically exported.
    Raises ``SciProofError`` unless the integrity verification of the manifest is ``PASS`` and every
    value can be read. Nothing is executed.
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
                if not location_matches_file(loc, out_path):
                    continue
                macro_name = _auto_macro_name(loc, out_path, used_names)
                auto_values.append((macro_name, out_path, loc))
                if len(auto_values) >= MAX_VALUES:
                    break
            if len(auto_values) >= MAX_VALUES:
                break
        if not auto_values:
            raise UsageError("no numeric outputs found in manifest to export as LaTeX macros")
        values = auto_values

    if len(values) > MAX_VALUES:
        raise UsageError(f"at most {MAX_VALUES} values per export")
    names = [name for name, _, _ in values]
    for name in names:
        if not MACRO_NAME.fullmatch(name):
            raise UsageError(
                f"--value {safe_text(name, 60)!r}: a LaTeX macro name is 1 to 40 letters "
                "(no digits, underscores or punctuation)"
            )
    if len(set(names)) != len(names):
        raise UsageError("a macro name is used more than once")
    base = manifest_path.parent
    declared = {o["path"]: o for o in manifest["outputs"]}
    for _, output, location in values:
        validate_relative_path(output)
        item = declared.get(output)
        if item is None or item.get("kind") == "directory" or not item.get("present", True):
            raise UsageError(f"{output!r} is not a declared output file of this manifest")
        if not location_matches_file(location, output):
            raise UsageError(
                f"{location!r} is not a location in {output!r}; use $.key[0].name for JSON or "
                "r2c1 for CSV/TSV (the form shown in difference reports)"
            )

    report = verify(manifest_path)
    if report.status is not Status.PASS:
        raise SciProofError(
            f"not exporting values: the verification of {manifest_path.name} is "
            f"{report.status.value} ({report.reason}). Run 'sciproof verify' to see why."
        )

    cache: dict[str, dict[str, object]] = {}
    lines = [
        f"% SciProof {__version__}: values of {_text_comment(manifest_path.name)} "
        f"(integrity verification: PASS)",
        "% Generated by 'sciproof export --format latex'; do not edit by hand.",
    ]
    for name, output, location in values:
        value: Any = _read(base, output, location, cache)
        lines.append(f"\\newcommand{{\\{name}}}{{{format_value(value)}}}")
    return "\n".join(lines) + "\n"


def _text_comment(text: str) -> str:
    """A file name for a ``%`` comment line: no line break may end the comment early."""
    return _CONTROL.sub(" ", text)

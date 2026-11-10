"""Record: capture an experiment without declaring anything.

``sciproof capture`` needs to be told which files are inputs and which are outputs, and whether an
output may be compared exactly or only within a tolerance. ``sciproof record`` finds that out by
running the command, and then captures it with what it found:

1. The command is run once with Python's audit hook installed (as ``suggest-inputs`` does). Files
   it **read** inside the working directory are inputs and files it **created or changed** are
   candidate outputs. A file that was both read and changed is state rather than a reproducible
   result, so it is not declared. Files below ``.git``, virtual environments, caches and
   SciProof's own files are never considered.
2. Unless ``--fast``, it is run a second time from the same starting state. An output that is
   identical both times is declared; a JSON, CSV or TSV output that differs only in its numbers is
   declared with a tolerance that covers the difference (ten times the spread, as ``calibrate``
   suggests) only when that tolerance is below 1; any other output that differs is not declared
   at all, because an exact hash of it could never be verified, and it is listed so that nothing
   is silently left out.
3. The command is captured for real, with those declarations.

So the command runs three times (twice with ``--fast`` skipping the second). Files that the
command created are removed between the runs so that each run starts from the same state; a file
that existed before and was rewritten cannot be restored, and it is excluded when it was also
read as command state.

This finds what Python code opened. Reads by C extensions through their own handles, by other
programs and over the network are invisible, and for a command that is not Python no input can
be detected at all: ``--input`` adds files by hand. What is recorded is a proposal that the report
shows in full; check it before relying on it.
"""

from __future__ import annotations

import contextlib
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sciproof.calibrate import _spread, derive_tolerances
from sciproof.capture import DEFAULT_MAX_LOG_BYTES, CaptureResult, capture
from sciproof.errors import CaptureError, CommandFailed
from sciproof.hashing import sha256_file
from sciproof.manifest import MANIFEST_SUFFIX, NAME_RE
from sciproof.numeric import Leaf, NumericFormatError, load_numeric
from sciproof.paths import to_manifest_path
from sciproof.runner import (
    CommandInterrupted,
    CommandNotRunnable,
    CommandTimeout,
    ExecResult,
    run_command,
)
from sciproof.suggest import suggest_inputs
from sciproof.textutil import safe_text

IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".ipynb_checkpoints",
    }
)
MAX_SCANNED_FILES = 50_000
MAX_DECLARED = 500
DEFAULT_RTOL = 1e-9
SAFETY_FACTOR = 10
NUMERIC_SUFFIXES = (".json", ".csv", ".tsv", ".npy", ".npz")
STDERR_TAIL_LINES = 10  # of a failed run, shown in the error
STDERR_TAIL_CHARACTERS = 2000


@dataclass
class RecordResult:
    capture: CaptureResult
    inputs: list[str]
    exact: list[str]
    numeric: list[str]
    tolerances: dict[str, dict[str, float]]
    excluded: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    command_line: str = ""


def _ignored(parts: tuple[str, ...]) -> bool:
    return any(
        part in IGNORED_DIRECTORIES or ".sciproof" in part or part.startswith(".sciproof-rerun-")
        for part in parts
    )


def _snapshot(base: Path) -> dict[str, tuple[int, int]]:
    """``{relative path: (size, mtime_ns)}`` of every regular file below ``base`` (no links)."""
    found: dict[str, tuple[int, int]] = {}
    for current, directories, files in os.walk(base, followlinks=False):
        relative = Path(current).relative_to(base)
        directories[:] = sorted(d for d in directories if not _ignored((*relative.parts, d)))
        for name in files:
            path = Path(current) / name
            if path.is_symlink() or _ignored((*relative.parts, name)):
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            found[(relative / name).as_posix()] = (info.st_size, info.st_mtime_ns)
            if len(found) > MAX_SCANNED_FILES:
                raise CaptureError(
                    f"more than {MAX_SCANNED_FILES} files below the working directory; run "
                    "'record' in a smaller directory or use 'capture' with declared files"
                )
    return found


def _existing_directories(base: Path) -> set[str]:
    """Relative paths of directories that exist before the run (POSIX format, no trailing slash)."""
    dirs: set[str] = set()
    for current, directories, _ in os.walk(base, followlinks=False):
        rel = Path(current).relative_to(base)
        dirs.update((rel / d).as_posix() for d in directories if not _ignored((*rel.parts, d)))
    return dirs


def _collapse_directories(
    base: Path,
    exact: list[str],
    numeric: list[str],
    excluded: list[tuple[str, str]],
    pre_existing_dirs: set[str],
) -> list[str]:
    """Collapse newly created directories into single directory entries when ALL files are exact."""
    numeric_set = set(numeric)
    excluded_set = {p for p, _ in excluded}

    candidate_dirs: set[str] = set()
    for p in exact:
        parts = p.split("/")
        for i in range(1, len(parts)):
            candidate_dirs.add("/".join(parts[:i]))

    valid_dirs: set[str] = set()
    for d in candidate_dirs:
        if d in pre_existing_dirs:
            continue
        prefix = d + "/"
        if any(num.startswith(prefix) for num in numeric_set):
            continue
        if any(exc.startswith(prefix) for exc in excluded_set):
            continue
        valid_dirs.add(d)

    if not valid_dirs:
        return exact

    sorted_dirs = sorted(valid_dirs, key=lambda x: (x.count("/"), len(x)))
    topmost_dirs: list[str] = []
    for d in sorted_dirs:
        if not any(d == top or d.startswith(top + "/") for top in topmost_dirs):
            topmost_dirs.append(d)

    new_exact: list[str] = []
    covered_files: set[str] = set()
    for d in topmost_dirs:
        prefix = d + "/"
        files_under = [p for p in exact if p.startswith(prefix)]
        if len(files_under) >= 2:
            new_exact.append(d)
            covered_files.update(files_under)

    for p in exact:
        if p not in covered_files:
            new_exact.append(p)

    return sorted(new_exact)


def _changed(
    before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]
) -> tuple[list[str], list[str]]:
    created = sorted(p for p in after if p not in before)
    modified = sorted(p for p in after if p in before and after[p] != before[p])
    return created, modified


def _remove_created(base: Path, created: list[str]) -> None:
    """Delete files the command created, and the directories that became empty because of it."""
    directories: set[Path] = set()
    for relative in created:
        path = base / relative
        path.unlink(missing_ok=True)
        directories.update(p for p in path.parents if p != base and base in p.parents)
    for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        with contextlib.suppress(OSError):
            directory.rmdir()  # only succeeds if it is empty


def _leaves(path: Path) -> list[Leaf] | None:
    if path.suffix.lower() not in NUMERIC_SUFFIXES:
        return None
    try:
        return load_numeric(path)
    except NumericFormatError:
        return None


def _failed(which: str, exit_code: int, stderr: bytes, hint: str) -> CommandFailed:
    """The error for a failed run, with the end of its stderr (escaped)."""
    text = stderr.decode("utf-8", "replace").rstrip()[-STDERR_TAIL_CHARACTERS:]
    lines = text.splitlines()[-STDERR_TAIL_LINES:]
    details = ["  last lines of its standard error:"] if lines else []
    details += [f"    {safe_text(line, STDERR_TAIL_CHARACTERS)}" for line in lines]
    return CommandFailed(
        f"the {which} run of the command failed (exit status {exit_code}); nothing was recorded",
        [*details, f"  ({hint})"],
    )


def _run(argv: list[str], base: Path, timeout: float | None) -> ExecResult:
    try:
        return run_command(argv, base, timeout)
    except CommandInterrupted as exc:
        raise CaptureError("interrupted; nothing was recorded") from exc
    except CommandTimeout as exc:
        raise CaptureError(f"{exc}; nothing was recorded") from exc
    except CommandNotRunnable as exc:
        raise CaptureError(str(exc)) from exc


def record(
    *,
    name: str,
    argv: list[str],
    base: Path | str,
    inputs: list[str] | None = None,
    exclude: list[str] | None = None,
    fast: bool = False,
    timeout: float | None = None,
    max_log_bytes: int = DEFAULT_MAX_LOG_BYTES,
    force: bool = False,
    note: str | None = None,
    record_hardware: bool = False,
    record_env: list[str] | None = None,
) -> RecordResult:
    """Find the inputs and outputs of ``argv`` by running it, then capture it with them."""
    if not NAME_RE.fullmatch(name):
        raise CaptureError(
            f"invalid name {name!r}: use letters, digits, '.', '_' or '-' (max 100 characters)"
        )
    if not argv or not argv[0]:
        raise CaptureError("no command given")
    base = Path(base).resolve()
    if (base / f"{name}{MANIFEST_SUFFIX}").exists() and not force:
        raise CaptureError(f"{name}{MANIFEST_SUFFIX} already exists (use --force to overwrite it)")
    extra_inputs = [to_manifest_path(base, p) for p in inputs or []]
    skipped = [to_manifest_path(base, p) for p in exclude or []]

    def is_skipped(relative: str) -> bool:
        return any(relative == s or relative.startswith(s + "/") for s in skipped)

    before = _snapshot(base)
    pre_existing_dirs = _existing_directories(base)
    found = suggest_inputs(argv, base, [], timeout)
    if found.exit_code != 0:
        raise _failed("first", found.exit_code, found.stderr, "record needs a run that succeeds")
    after_first = _snapshot(base)
    created, modified = _changed(before, after_first)
    written = [p for p in created + modified if not is_skipped(p)]
    if not written:
        raise CaptureError(
            "the command created or changed no file below the working directory, so there is "
            "nothing to record; declare the outputs with 'sciproof capture'"
        )
    first_digest = {p: sha256_file(base / p)[0] for p in written}
    first_leaves = {p: _leaves(base / p) for p in written}
    _remove_created(base, created)

    notes: list[str] = []
    excluded: list[tuple[str, str]] = []
    second_digest: dict[str, str] = {}
    second_leaves: dict[str, list[Leaf] | None] = {}
    if not fast:
        second = _run(argv, base, timeout)
        if second.exit_code != 0:
            raise _failed(
                "second",
                second.exit_code,
                second.stderr,
                "a command that is not repeatable from the same starting state cannot be recorded",
            )
        after_second = _snapshot(base)
        created_again, modified_again = _changed(before, after_second)
        for relative in created_again + modified_again:
            if relative in first_digest and (base / relative).is_file():
                second_digest[relative] = sha256_file(base / relative)[0]
                second_leaves[relative] = _leaves(base / relative)
        for relative in created_again + modified_again:
            if relative not in first_digest and not is_skipped(relative):
                notes.append(f"{relative} was written by the second run only; it is not declared")
        _remove_created(base, created_again)
    else:
        notes.append("determinism was not checked (--fast): every output is assumed stable")

    read_and_written = set(found.inside) & set(written)
    exact: list[str] = []
    numeric: list[str] = []
    tolerances: dict[str, dict[str, float]] = {}
    for relative in written:
        if relative in read_and_written:
            excluded.append(
                (relative, "read and written by the command: it is state, not a result")
            )
            continue
        parsed = first_leaves[relative]
        if parsed is None and relative.lower().endswith((".npy", ".npz")):
            try:
                import numpy  # noqa: F401
            except ImportError:
                excluded.append(
                    (
                        relative,
                        "comparing this file needs NumPy: pip install 'sciproof[numpy]'",
                    )
                )
                continue
        if fast or second_digest.get(relative) == first_digest[relative]:
            (numeric if parsed is not None else exact).append(relative)
        elif relative not in second_digest:
            excluded.append((relative, "not written by the second run"))
        elif parsed is None or second_leaves.get(relative) is None:
            excluded.append(
                (
                    relative,
                    "differs between two identical runs and is not JSON/CSV/TSV/NPY/NPZ",
                )
            )
        else:
            samples = [parsed, second_leaves[relative] or []]
            _, _, _, problem = _spread(samples, 0.0, 0.0)
            if problem:
                excluded.append((relative, f"differs between two identical runs: {problem}"))
            else:
                suggested_rtol, suggested_atol = derive_tolerances(samples)
                if suggested_rtol is not None and suggested_rtol >= 1:
                    excluded.append(
                        (
                            relative,
                            "differs between two identical runs: "
                            f"derived rtol {suggested_rtol:g} is 1 or more",
                        )
                    )
                else:
                    numeric.append(relative)
                    tol: dict[str, float] = {}
                    if suggested_rtol is not None:
                        tol["rtol"] = suggested_rtol
                    if suggested_atol is not None:
                        tol["atol"] = suggested_atol
                    if tol:
                        tolerances[relative] = tol

    exact = _collapse_directories(base, exact, numeric, excluded, pre_existing_dirs)
    declared_outputs = set(exact) | set(numeric)
    never_inputs = set(first_digest) | set(second_digest)
    detected = [p for p in found.inside if p not in never_inputs and not is_skipped(p)]
    chosen_inputs = sorted(
        {
            p
            for p in detected + extra_inputs
            if (base / p).is_file()
            and p not in declared_outputs
            and not any(p == d or p.startswith(d + "/") for d in exact if (base / d).is_dir())
        }
    )
    if not found.hook_loaded:
        notes.append(
            "no input could be detected: the command is not run by Python (or Python ran with -I "
            "or -S). Declare inputs with --input."
        )
    if len(chosen_inputs) > MAX_DECLARED or len(declared_outputs) > MAX_DECLARED:
        raise CaptureError(
            f"more than {MAX_DECLARED} inputs or outputs were found; declare a directory with "
            "'sciproof capture --input DIR' instead"
        )
    if not declared_outputs and not chosen_inputs:
        raise CaptureError("every output was excluded and no input was found; nothing to record")

    result = capture(
        name=name,
        argv=argv,
        base=base,
        inputs=chosen_inputs,
        outputs=exact,
        numeric_outputs=numeric,
        rtol=DEFAULT_RTOL,
        tolerances=tolerances or None,
        timeout=timeout,
        max_log_bytes=max_log_bytes,
        force=force,
        note=note,
        record_hardware=record_hardware,
        record_env=record_env,
    )
    return RecordResult(
        capture=result,
        inputs=chosen_inputs,
        exact=sorted(exact),
        numeric=sorted(numeric),
        tolerances=tolerances,
        excluded=excluded,
        notes=notes,
        command_line=_command_line(
            name, argv, chosen_inputs, exact, numeric, tolerances, record_env
        ),
    )


def _command_line(
    name: str,
    argv: list[str],
    inputs: list[str],
    exact: list[str],
    numeric: list[str],
    tolerances: dict[str, dict[str, Any]],
    record_env: list[str] | None = None,
) -> str:
    parts = ["sciproof", "capture", "--name", name]
    for flag, paths in (("--input", inputs), ("--output", exact), ("--output-numeric", numeric)):
        for path in sorted(paths):
            parts += [flag, path]
    for env_name in record_env or []:
        parts += ["--record-env", env_name]
    for path, settings in sorted(tolerances.items()):
        parts += ["--tolerance", path, " ".join(f"{k}={v:g}" for k, v in settings.items())]
    return shlex.join([*parts, "--", *argv])

"""Path handling.

Manifests are untrusted input, so every path read from one is validated before it is used.
Paths in a manifest are always relative, use ``/`` as separator and must stay inside the
directory that contains the manifest (the *base directory*).
"""

from __future__ import annotations

import stat
from pathlib import Path, PurePosixPath, PureWindowsPath

from sciproof.errors import UnsafePathError


def validate_relative_path(value: object, *, allow_dot: bool = False) -> str:
    """Check that ``value`` is a safe relative path string and return it unchanged."""
    if not isinstance(value, str) or not value:
        raise UnsafePathError(f"path must be a non-empty string, got {value!r}")
    if "\x00" in value:
        raise UnsafePathError("path contains a NUL byte")
    if "\\" in value:
        raise UnsafePathError(f"path {value!r} contains a backslash; use '/' as separator")
    if value == ".":
        if allow_dot:
            return value
        raise UnsafePathError("path '.' is not a file")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise UnsafePathError(f"path {value!r} is absolute")
    if PureWindowsPath(value).drive:
        raise UnsafePathError(f"path {value!r} has a drive letter")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise UnsafePathError(f"path {value!r} contains empty, '.' or '..' components")
    for part in parts:
        _check_portable_component(value, part)
    return value


_WINDOWS_DEVICE_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{n}" for n in range(1, 10)),
        *(f"LPT{n}" for n in range(1, 10)),
    }
)


def _check_portable_component(path: str, part: str) -> None:
    """Refuse names that mean something else on Windows.

    Windows ignores trailing dots and spaces (``trail.`` is the file ``trail``, ``...`` is the
    current directory) and treats ``CON``, ``NUL``, ``COM1`` ... (with any extension) as devices.
    A manifest has to mean the same thing on every platform, so such names are not accepted.
    """
    if any(ord(char) < 32 for char in part):
        raise UnsafePathError(f"path {path!r} contains a control character")
    if part.endswith((".", " ")):
        raise UnsafePathError(f"path {path!r} has a component ending with '.' or a space")
    if part.split(".", 1)[0].upper() in _WINDOWS_DEVICE_NAMES:
        raise UnsafePathError(f"path {path!r} uses a reserved Windows device name")


def resolve_inside(base: Path, relative: str) -> Path:
    """Resolve ``relative`` below ``base``, refusing anything that escapes it.

    Symbolic links are followed before the check, so a link that points outside ``base``
    is rejected too.
    """
    validate_relative_path(relative)
    base_resolved = base.resolve()
    candidate = (base_resolved / relative).resolve()
    if not candidate.is_relative_to(base_resolved):
        raise UnsafePathError(f"path {relative!r} resolves outside of {base_resolved}")
    return candidate


def to_manifest_path(base: Path, user_path: str | Path) -> str:
    """Convert a path given on the command line to a safe manifest path (relative to ``base``)."""
    base_resolved = base.resolve()
    raw = Path(user_path)
    candidate = (raw if raw.is_absolute() else base_resolved / raw).resolve()
    if not candidate.is_relative_to(base_resolved):
        raise UnsafePathError(
            f"{str(user_path)!r} is outside of the working directory {base_resolved}"
        )
    relative = candidate.relative_to(base_resolved).as_posix()
    return validate_relative_path(relative)


def has_glob_magic(pattern: str | Path) -> bool:
    """Return True if the pattern contains glob wildcards (*, ?, or [])."""
    s = str(pattern)
    return any(c in s for c in ("*", "?", "["))


def _is_sciproof_artifact(relative: str) -> bool:
    """Return True for files or directories containing SciProof evidence."""
    parts = relative.split("/")

    # Evidence manifests and signatures are files, not directory filters.
    if parts[-1].endswith((".sciproof.json", ".sig")):
        return True

    # Snapshot and rerun directories exclude everything inside them.
    return any(
        part.endswith((".sciproof.d", ".sciproof.d.tmp"))
        or part.startswith(".sciproof-rerun-")
        for part in parts[:-1]
    )


def expand_glob(base: Path, pattern: str) -> list[str]:
    """Expand a glob pattern safely below base directory, returning relative POSIX paths.

    Only regular files are returned. Directories matched by a recursive
    pattern are dropped, never hashed as files. A symbolic link is returned
    by its own path, not by its resolved target path, provided its target
    is a regular file inside ``base``; links to directories are not
    followed into the directory, and links whose target is outside ``base``
    or missing are dropped. Dropping here is deliberate: a pattern whose
    matches are all dropped expands to nothing, which the caller (capture)
    must treat as an unmatched glob, not as a silently empty declaration.
    """
    base_resolved = base.resolve()
    norm = pattern.replace("\\", "/")
    if norm.startswith("/"):
        raise UnsafePathError(f"glob pattern {pattern!r} is absolute")
    parts = norm.split("/")
    if any(p in ("..", "") for p in parts):
        raise UnsafePathError(f"glob pattern {pattern!r} contains invalid or '..' components")
    matches: list[str] = []
    for match in sorted(base_resolved.glob(norm)):
        try:
            # Do not follow a symlinked directory in the middle of a match:
            # a pattern traversing linkdir/ must not descend into it.
            rel_parts = match.relative_to(base_resolved).parts
            if any(
                (base_resolved.joinpath(*rel_parts[:i])).is_symlink()
                for i in range(1, len(rel_parts))
            ):
                continue
            mode = match.lstat().st_mode
            if stat.S_ISLNK(mode):
                # Record the link itself, not its target: resolve only to
                # check that the target is a regular file inside base.
                target = match.resolve()
                if not target.is_relative_to(base_resolved) or not target.is_file():
                    continue
            elif stat.S_ISREG(mode):
                # A plain regular file.
                pass
            else:
                # Directories, devices, sockets and other special files.
                continue
            rel = match.relative_to(base_resolved).as_posix()
            validate_relative_path(rel)
            if _is_sciproof_artifact(rel):
                continue
            if rel not in matches:
                matches.append(rel)
        except (UnsafePathError, OSError):
            continue
    return matches

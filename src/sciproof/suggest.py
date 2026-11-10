"""Suggest inputs: run a Python command once and list the files it read that were not declared.

A small ``sitecustomize.py`` is put first on ``PYTHONPATH``; it installs an audit hook
(``sys.addaudithook``) that appends the path of every file opened for reading to a log. This
sees what Python code opens (including imported modules). It does **not** see reads done by
C extensions through their own file handles, reads by non-Python subprocesses, network access
or environment variables, so an empty answer is not proof that nothing was missed.

The hook announces itself in the log when it is loaded. If that line is missing (the command was
not Python, or Python ran with ``-I``/``-S`` and ignored ``PYTHONPATH``), nothing was observed and
the result says so instead of reporting "no undeclared files". Paths are written hex-encoded, one
per line, so a file name containing a line break cannot corrupt the log.
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from sciproof.errors import CaptureError
from sciproof.paths import to_manifest_path
from sciproof.runner import CommandNotRunnable, CommandTimeout, run_command

LOG_ENV = "SCIPROOF_AUDIT_LOG"
HOOK_MARKER = b"#hook-loaded"

_SITECUSTOMIZE = r"""
import os, sys
_log = os.environ.get("SCIPROOF_AUDIT_LOG")
if _log:
    _fd = os.open(_log, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0))
    os.write(_fd, b"#hook-loaded\n")
    def _hook(event, args):
        if event != "open":
            return
        try:
            path, mode, flags = args
            if not isinstance(path, (str, bytes, os.PathLike)):
                return
            writing = (flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC)
                       if isinstance(flags, int) else 0) or (
                       mode is not None and any(c in str(mode) for c in "wax+"))
            if writing:
                return
            os.write(_fd, os.fsencode(os.path.abspath(os.fsdecode(path))).hex().encode() + b"\n")
        except Exception:
            pass
    sys.addaudithook(_hook)

# Chain to the original or next sitecustomize on sys.path if one exists:
_this_dir = os.path.normcase(os.path.abspath(os.path.dirname(__file__)))
_saved_sys_path = sys.path[:]
_saved_module = sys.modules.get("sitecustomize")
try:
    sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != _this_dir]
    sys.modules.pop("sitecustomize", None)
    import sitecustomize
except ImportError:
    if _saved_module is not None:
        sys.modules["sitecustomize"] = _saved_module
finally:
    sys.path[:] = _saved_sys_path
"""

_IGNORED_PARTS = {"__pycache__", ".git", ".hg", ".venv", "venv", "site-packages"}


@dataclass
class Suggestions:
    exit_code: int
    #: False if the audit hook never started, so nothing was observed at all.
    hook_loaded: bool = True
    inside: list[str] = field(default_factory=list)  # undeclared files below the base directory
    outside: list[str] = field(default_factory=list)  # undeclared files elsewhere
    stderr: bytes = b""  # the command's standard error, as it wrote it


def _python_prefixes() -> list[Path]:
    prefixes = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    return [Path(p).resolve() for p in prefixes if p]


def _is_declared(path: Path, declared: list[Path]) -> bool:
    return any(path == item or path.is_relative_to(item) for item in declared)


def suggest_inputs(
    argv: list[str],
    base: Path,
    declared: list[str],
    timeout: float | None = None,
) -> Suggestions:
    """Run ``argv`` once and report files read but not covered by ``declared`` paths."""
    base = base.resolve()
    declared_paths = [(base / to_manifest_path(base, item)).resolve() for item in declared]
    prefixes = _python_prefixes()
    with tempfile.TemporaryDirectory(prefix="sciproof-audit-") as tmp:
        tmp_path = Path(tmp).resolve()
        (tmp_path / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
        log = tmp_path / "reads.log"
        log.touch()
        # The command gets its own copy of the environment; this process's is never touched.
        env = dict(os.environ)
        env[LOG_ENV] = str(log)
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(tmp_path) + (os.pathsep + existing if existing else "")
        try:
            result = run_command(argv, base, timeout, env)
        except CommandTimeout as exc:
            raise CaptureError(f"{exc}; nothing was recorded") from exc
        except CommandNotRunnable as exc:
            raise CaptureError(str(exc)) from exc
        lines = log.read_bytes().split(b"\n")

    hook_loaded = HOOK_MARKER in lines

    seen: set[Path] = set()
    inside: list[str] = []
    outside: list[str] = []
    for raw in lines:
        if not raw or raw == HOOK_MARKER:
            continue
        try:
            path = Path(os.fsdecode(bytes.fromhex(raw.decode("ascii"))))
        except ValueError:
            continue  # not a line written by the hook
        if path.suffix == ".pyc" and path.parent.name == "__pycache__":
            # a cached import: the module's source is what the experiment depends on
            path = path.parent.parent / (path.name.split(".", 1)[0] + ".py")
        try:
            path = path.resolve()
        except OSError:
            continue
        if path in seen or not path.is_file() or tmp_path in path.parents:
            continue
        seen.add(path)
        if _IGNORED_PARTS & set(path.parts) or path.suffix == ".pyc":
            continue
        if _is_declared(path, declared_paths):
            continue
        if path.is_relative_to(base):
            relative = path.relative_to(base)
            # SciProof's own files: manifests, snapshot directories, rerun directories
            if not any(".sciproof" in part for part in relative.parts):
                inside.append(relative.as_posix())
        elif not any(path.is_relative_to(prefix) for prefix in prefixes):
            outside.append(str(path))
    return Suggestions(
        result.exit_code, hook_loaded, sorted(inside), sorted(outside), result.stderr
    )

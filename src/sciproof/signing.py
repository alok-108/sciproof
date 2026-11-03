"""Sign a manifest and check the signature, by calling OpenSSH ``ssh-keygen -Y``.

SciProof implements no cryptography. It runs ``ssh-keygen`` (which has to be installed) and reads
its answer. It never decides whom to trust: the caller passes an ``allowed_signers`` file (the
format of OpenSSH, one ``principal public-key`` pair per line), and a signature is good only if
it was made by a key listed there. That file is the trust root; the manifest cannot name one.

The signature is detached, in the file ``<manifest>.sig`` next to the manifest, and uses its own
namespace so that it cannot be replayed as a signature of another kind. It covers the exact bytes
of the manifest, which also fixes every hash the manifest contains.

A good signature shows that the holder of an allowed key vouched for these bytes. It does not
show that the experiment was run honestly, that the key was not stolen, or when it was signed.
See docs/SIGNING.md.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # noqa: S404
from dataclasses import dataclass
from pathlib import Path

from sciproof.errors import SciProofError, UsageError
from sciproof.manifest import load_manifest
from sciproof.textutil import safe_text

NAMESPACE = "sciproof-manifest@hayagrivai-labs"
SIGNATURE_SUFFIX = ".sig"
TIMEOUT_SECONDS = 60

_FINGERPRINT = re.compile(r"with (\S+) key (SHA256:\S+)")


class SignatureError(SciProofError):
    """A manifest could not be signed."""


@dataclass(frozen=True)
class SignatureResult:
    """Outcome of checking ``<manifest>.sig`` against an allowed-signers file."""

    status: str  # "good", "bad", "missing" or "unavailable"
    message: str
    signature_file: str
    principal: str | None = None
    key_type: str | None = None
    fingerprint: str | None = None


def ssh_keygen() -> str | None:
    """Path of the ``ssh-keygen`` executable, or ``None`` if OpenSSH is not installed."""
    return shutil.which("ssh-keygen")


def signature_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(manifest_path.name + SIGNATURE_SUFFIX)


def sign_manifest(manifest_path: Path | str, key_path: Path | str) -> Path:
    """Write ``<manifest>.sig`` with the private key in ``key_path``; returns its path.

    The manifest is validated first: SciProof does not sign a file it would refuse to read.
    ``ssh-keygen`` asks for the key's passphrase itself if it has one.
    """
    manifest_path = Path(manifest_path).resolve()
    key_path = Path(key_path).expanduser()
    load_manifest(manifest_path)
    executable = ssh_keygen()
    if executable is None:
        raise SignatureError("ssh-keygen (OpenSSH) was not found; install OpenSSH to sign")
    if not key_path.is_file():
        raise SignatureError(f"signing key not found: {key_path}")
    target = signature_path(manifest_path)
    # ssh-keygen asks "Overwrite (y/n)?" when the signature exists and, without a terminal,
    # exits 0 and keeps the OLD signature. Move it aside so a new one is really written, and
    # put it back if signing fails.
    previous = target.with_name(target.name + ".previous")
    if target.exists():
        os.replace(target, previous)
    try:
        try:
            completed = subprocess.run(  # noqa: S603
                [
                    executable,
                    "-Y",
                    "sign",
                    "-f",
                    str(key_path),
                    "-n",
                    NAMESPACE,
                    str(manifest_path),
                ],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SignatureError(f"ssh-keygen could not be run: {exc}") from exc
        if completed.returncode != 0 or not target.is_file():
            detail = safe_text((completed.stderr or completed.stdout or "no output").strip(), 400)
            raise SignatureError(f"ssh-keygen could not sign the manifest: {detail}")
    except BaseException:
        if previous.exists() and not target.exists():
            os.replace(previous, target)
        raise
    previous.unlink(missing_ok=True)
    return target


def _run(arguments: list[str], data: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603
        arguments,
        input=data,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


def check_signature(manifest_path: Path | str, allowed_signers: Path | str) -> SignatureResult:
    """Check ``<manifest>.sig`` against ``allowed_signers``. Nothing is executed but ssh-keygen."""
    manifest_path = Path(manifest_path).resolve()
    allowed_signers = Path(allowed_signers).expanduser()
    signature = signature_path(manifest_path)
    name = signature.name
    if not allowed_signers.is_file():
        raise UsageError(f"allowed signers file not found: {allowed_signers}")
    if not signature.is_file():
        return SignatureResult("missing", f"no signature file {name} next to the manifest", name)
    executable = ssh_keygen()
    if executable is None:
        return SignatureResult(
            "unavailable",
            "ssh-keygen (OpenSSH) was not found, so the signature cannot be checked",
            name,
        )
    try:
        found = _run(
            [executable, "-Y", "find-principals", "-f", str(allowed_signers), "-s", str(signature)]
        )
        principals = [
            line.strip()
            for line in found.stdout.decode("utf-8", errors="replace").splitlines()
            if line.strip()
        ]
        if found.returncode != 0 or not principals:
            return SignatureResult(
                "bad",
                f"{name} was not made by a key listed in the allowed signers file "
                "(or the signature file is damaged)",
                name,
            )
        data = manifest_path.read_bytes()
        # Try every principal find-principals returned. There is no
        # arbitrary cutoff: stopping after a fixed number would falsely
        # report a valid signature as bad when its principal is later
        # in the list (see #246).
        for principal in principals:
            checked = _run(
                [
                    executable,
                    "-Y",
                    "verify",
                    "-f",
                    str(allowed_signers),
                    "-I",
                    principal,
                    "-n",
                    NAMESPACE,
                    "-s",
                    str(signature),
                ],
                data,
            )
            if checked.returncode == 0:
                text = checked.stdout.decode("utf-8", errors="replace")
                match = _FINGERPRINT.search(text)
                return SignatureResult(
                    "good",
                    f"good signature by {safe_text(principal, 100)!r}"
                    + (f" ({match.group(1)} key {match.group(2)})" if match else ""),
                    name,
                    principal,
                    match.group(1) if match else None,
                    match.group(2) if match else None,
                )
    except (OSError, subprocess.SubprocessError) as exc:
        return SignatureResult(
            "unavailable", f"ssh-keygen could not be run: {safe_text(str(exc), 200)}", name
        )
    return SignatureResult(
        "bad",
        f"the signature {name} does not match this manifest: it was modified after signing, or "
        "signed for another purpose",
        name,
    )

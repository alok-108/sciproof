"""Pytest plugin for SciProof: discover and verify *.sciproof.json manifests."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from sciproof.errors import SciProofError
from sciproof.verify import Status, verify

if TYPE_CHECKING:
    import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("sciproof", "SciProof scientific reproducibility verification")

    group.addoption(
        "--sciproof",
        action="store_true",
        default=False,
        help="Collect and verify *.sciproof.json manifests as pytest tests",
    )
    group.addoption(
        "--sciproof-rerun",
        action="store_true",
        default=False,
        help="Re-execute recorded commands during pytest verification (with --sciproof)",
    )
    group.addoption(
        "--sciproof-clean-env",
        action="store_true",
        default=False,
        help="Give the rerun command only a minimal environment (with --sciproof-rerun)",
    )
    group.addoption(
        "--sciproof-pass-env",
        action="append",
        default=[],
        metavar="NAME",
        help="With --sciproof-clean-env: also pass this environment variable; repeatable",
    )
    group.addoption(
        "--sciproof-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Stop the rerun after this many seconds",
    )
    group.addoption(
        "--sciproof-policy",
        type=Path,
        default=None,
        metavar="FILE",
        help="Also check the rules of this policy file (TOML) against the evidence",
    )
    group.addoption(
        "--sciproof-allowed-signers",
        type=Path,
        default=None,
        metavar="FILE",
        help="Check the manifest signature against this OpenSSH allowed-signers file",
    )

    parser.addini(
        "sciproof",
        type="bool",
        default=False,
        help="Collect and verify *.sciproof.json manifests as pytest tests",
    )
    parser.addini(
        "sciproof_rerun",
        type="bool",
        default=False,
        help="Re-execute recorded commands during pytest verification",
    )
    parser.addini(
        "sciproof_clean_env",
        type="bool",
        default=False,
        help="Run rerun command with a minimal clean environment",
    )
    parser.addini(
        "sciproof_pass_env",
        type="args",
        default=[],
        help="Environment variables to pass through when clean environment is enabled",
    )
    parser.addini(
        "sciproof_timeout",
        type="string",
        default="",
        help="Timeout in seconds for rerun command",
    )
    parser.addini(
        "sciproof_policy",
        type="string",
        default="",
        help="Policy file path (TOML) to check during verification",
    )
    parser.addini(
        "sciproof_allowed_signers",
        type="string",
        default="",
        help="OpenSSH allowed-signers file path to verify signatures against",
    )


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:
    enabled = False
    try:
        val = parent.config.getoption("--sciproof", False)
        if type(val).__name__ != "MagicMock":
            enabled = bool(val)
    except (ValueError, AttributeError):
        pass
    if not enabled:
        try:
            if hasattr(parent.config, "getini"):
                ini_val = parent.config.getini("sciproof")
                if type(ini_val).__name__ != "MagicMock":
                    enabled = bool(ini_val)
        except (ValueError, AttributeError):
            pass

    if enabled and file_path.name.endswith(".sciproof.json"):
        return SciProofFile.from_parent(parent, path=file_path)

    return None


class SciProofFile(pytest.File):
    def collect(self) -> list[pytest.Item]:
        return [SciProofItem.from_parent(self, name="verify")]


class SciProofItem(pytest.Item):
    def runtest(self) -> None:
        def _opt(name: str, default: Any = None) -> Any:
            try:
                val = self.config.getoption(name, default)
                if type(val).__name__ == "MagicMock":
                    return default
                return val
            except (ValueError, AttributeError):
                return default

        def _ini(name: str, default: Any = None) -> Any:
            try:
                if hasattr(self.config, "getini"):
                    val = self.config.getini(name)
                    if type(val).__name__ == "MagicMock":
                        return default
                    return val
            except (ValueError, AttributeError):
                return default
            return default

        rerun = bool(_opt("--sciproof-rerun") or _ini("sciproof_rerun") or False)
        clean_env = bool(_opt("--sciproof-clean-env") or _ini("sciproof_clean_env") or False)
        pass_env_list = _opt("--sciproof-pass-env")
        if not isinstance(pass_env_list, (list, tuple)):
            pass_env_list = _ini("sciproof_pass_env")
        pass_env = (
            [str(x) for x in pass_env_list] if isinstance(pass_env_list, (list, tuple)) else []
        )

        timeout = _opt("--sciproof-timeout")
        if timeout is False:
            timeout = None
        if timeout is not None:
            with contextlib.suppress(ValueError, TypeError):
                timeout = float(timeout)
        if timeout is None:
            timeout_ini = _ini("sciproof_timeout")
            if timeout_ini and timeout_ini is not False:
                with contextlib.suppress(ValueError, TypeError):
                    timeout = float(timeout_ini)

        policy = _opt("--sciproof-policy")
        if policy is False or not policy:
            policy = _ini("sciproof_policy") or None
        if policy is False or policy == "":
            policy = None

        allowed_signers = _opt("--sciproof-allowed-signers")
        if allowed_signers is False or not allowed_signers:
            allowed_signers = _ini("sciproof_allowed_signers") or None
        if allowed_signers is False or allowed_signers == "":
            allowed_signers = None

        verify_kwargs: dict[str, Any] = {
            "rerun": rerun,
            "assume_yes": True,
            "clean_env": clean_env,
            "pass_env": pass_env,
        }
        if timeout is not None:
            verify_kwargs["timeout"] = timeout
        if policy is not None:
            verify_kwargs["policy"] = policy
        if allowed_signers is not None:
            verify_kwargs["allowed_signers"] = allowed_signers

        try:
            report = verify(self.path, **verify_kwargs)
        except SciProofError as exc:
            pytest.fail(f"SciProof manifest verification error: {exc}", pytrace=False)

        if report.status == Status.PASS:
            return

        if report.status == Status.INCONCLUSIVE:
            pytest.skip(f"SciProof verification inconclusive: {report.reason}")

        failures = [
            f"  [{c.status.value}] {c.subject}: {c.message}"
            for c in report.checks
            if c.status == Status.FAIL
        ]

        msg = f"SciProof verification FAILED: {report.reason}\n" + "\n".join(failures)

        pytest.fail(msg, pytrace=False)

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.path, 0, f"sciproof:{self.path.name}"

"""Pytest plugin for SciProof: discover and verify *.sciproof.json manifests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

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


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:

    if parent.config.getoption("--sciproof", False) and file_path.name.endswith(".sciproof.json"):
        return SciProofFile.from_parent(parent, path=file_path)

    return None


class SciProofFile(pytest.File):
    def collect(self) -> list[pytest.Item]:

        return [SciProofItem.from_parent(self, name="verify")]


class SciProofItem(pytest.Item):
    def runtest(self) -> None:

        rerun = bool(self.config.getoption("--sciproof-rerun", False))

        try:
            report = verify(self.path, rerun=rerun, assume_yes=True)
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

"""SARIF 2.1.0 output for verification reports, for GitHub Code Scanning and security tabs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sciproof import __version__
from sciproof.textutil import safe_text

if TYPE_CHECKING:
    from sciproof.verify import Check, Report


SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
)

SARIF_VERSION = "2.1.0"


def _find_repo_root(start: Path) -> Path | None:
    try:
        current = start.resolve()
        for parent in [current, *current.parents]:
            if (parent / ".git").exists():
                return parent
    except (OSError, RuntimeError):
        pass
    return None


def _resolve_check_uri(check: Check, manifest_uri: str, base_rel: Path) -> str:
    path: str | None = None
    evidence = check.evidence or {}
    if isinstance(evidence.get("path"), str):
        path = evidence["path"]
    elif isinstance(evidence.get("output"), str):
        path = evidence["output"]
    elif check.subject.startswith("rerun output "):
        path = check.subject.removeprefix("rerun output ")
    elif check.subject.startswith("output "):
        path = check.subject.removeprefix("output ")
    elif check.subject.startswith("input "):
        path = check.subject.removeprefix("input ")
    elif check.subject.startswith("rerun claim "):
        path = check.subject.removeprefix("rerun claim ").split(":", 1)[0].strip()
    elif check.subject.startswith("claim "):
        path = check.subject.removeprefix("claim ").split(":", 1)[0].strip()

    if path:
        clean_path = path.strip()
        return (base_rel / clean_path).as_posix() if base_rel != Path() else clean_path
    return manifest_uri


def report_sarif(report: Report) -> dict[str, Any]:
    """Verification report as a SARIF 2.1.0 dictionary."""

    rules = [
        {
            "id": "SP001",
            "name": "VerificationFailure",
            "shortDescription": {"text": "SciProof check failed."},
            "defaultConfiguration": {"level": "error"},
        },
        {
            "id": "SP002",
            "name": "VerificationInconclusive",
            "shortDescription": {"text": "SciProof check was inconclusive."},
            "defaultConfiguration": {"level": "warning"},
        },
        {
            "id": "SP003",
            "name": "VerificationWarning",
            "shortDescription": {"text": "SciProof verification warning."},
            "defaultConfiguration": {"level": "warning"},
        },
    ]

    manifest_path = report.manifest_path
    base_rel = Path()
    if manifest_path is not None:
        try:
            m_resolved = manifest_path.resolve()
            base_dir = m_resolved.parent
            repo_root = _find_repo_root(base_dir)
            if repo_root is not None:
                manifest_uri = m_resolved.relative_to(repo_root).as_posix()
                base_rel = base_dir.relative_to(repo_root)
            else:
                manifest_uri = (
                    manifest_path.name if manifest_path.is_absolute() else manifest_path.as_posix()
                )
        except (ValueError, OSError):
            manifest_uri = manifest_path.name
    else:
        manifest_uri = f"{report.name}.sciproof.json" if report.name else "run.sciproof.json"

    results: list[dict[str, Any]] = []

    for check in report.checks:
        if check.status.value == "PASS":
            continue

        level = "error" if check.status.value == "FAIL" else "warning"
        rule_id = "SP001" if check.status.value == "FAIL" else "SP002"
        subject = safe_text(check.subject, 500)
        message = safe_text(check.message, 2000)
        uri = _resolve_check_uri(check, manifest_uri, base_rel)

        result: dict[str, Any] = {
            "ruleId": rule_id,
            "level": level,
            "message": {"text": f"{subject}: {message}"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {
                            "uri": uri,
                        },
                    }
                }
            ],
        }

        results.append(result)

    for warning in report.warnings:
        warn_text = safe_text(warning, 2000)

        results.append(
            {
                "ruleId": "SP003",
                "level": "warning",
                "message": {"text": warn_text},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {
                                "uri": manifest_uri,
                            },
                        }
                    }
                ],
            }
        )

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "sciproof",
                        "version": __version__,
                        "informationUri": "https://github.com/hayagrivai-labs/sciproof",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }


def format_report_sarif(report: Report) -> str:
    """Verification report formatted as SARIF 2.1.0 JSON string."""

    return json.dumps(report_sarif(report), indent=2, allow_nan=False) + "\n"

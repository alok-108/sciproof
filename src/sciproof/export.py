"""Export a manifest for other tools: an in-toto Statement (v1), DSSE, or OpenLineage run events.

The Statement lists the output files as subjects with their SHA-256 and describes the run in a
SciProof-specific predicate. It is **not signed and not an attestation by itself**: in-toto
Statements are normally wrapped in a signed envelope (DSSE) by the tool that attests them, and
that step is deliberately left to such tools (e.g. Cosign, Sigstore, or GitHub Attestations).
The predicate type is SciProof's own; it does not claim to be SLSA provenance, because SciProof
records an experiment, not a build.

The OpenLineage export describes the run as a pair of run events (START and COMPLETE or FAIL)
with the inputs and outputs as datasets. It carries lineage (what was read and written, and
whether the command succeeded) but not hashes or a verdict, because no standard facet for
those was verified; use the in-toto export or the manifest for that.

Nothing is executed and only the manifest is read.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sciproof import __version__
from sciproof.claims import claim_text
from sciproof.errors import UsageError
from sciproof.manifest import load_manifest

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://hayagrivai-labs.github.io/sciproof/docs/export/#experiment-v1"
DSSE_PAYLOAD_TYPE = "application/vnd.in-toto+json"
# The schema URL as shown in the OpenLineage specification's own RunEvent example.
OPENLINEAGE_SCHEMA_URL = "https://openlineage.io/spec/1-0-0/OpenLineage.json#/definitions/RunEvent"
OPENLINEAGE_PRODUCER = "https://github.com/hayagrivai-labs/sciproof"
OPENLINEAGE_JOB_NAMESPACE = "sciproof"
OPENLINEAGE_DATASET_NAMESPACE = "sciproof-files"


def _subjects(items: list[dict[str, Any]], *, only_present: bool) -> list[dict[str, Any]]:
    """One subject per file; the files of a directory are listed as ``dir/file``."""
    result = []
    for item in items:
        if only_present and not item.get("present", True):
            continue
        if item.get("kind") == "directory":
            for entry in item.get("files") or []:
                result.append(
                    {
                        "name": f"{item['path']}/{entry['path']}",
                        "digest": {"sha256": entry["sha256"]},
                    }
                )
        else:
            result.append({"name": item["path"], "digest": {"sha256": item["sha256"]}})
    return result


def to_in_toto(manifest_path: Path | str) -> dict[str, Any]:
    """The manifest as an in-toto Statement v1 (a dict that ``json.dumps`` can write)."""
    manifest_path = Path(manifest_path).resolve()
    manifest = load_manifest(manifest_path)
    environment = manifest["environment"]
    git = manifest.get("git")
    env_vars = dict(environment.get("env_vars", {}))
    recorded_env = environment.get("recorded_env")
    if recorded_env:
        for k, v in recorded_env.items():
            if v is not None and k not in env_vars:
                env_vars[k] = v
    predicate: dict[str, Any] = {
        "experiment": manifest["name"],
        "capturedAt": manifest["created_at"],
        "producer": {
            "name": "sciproof",
            "version": __version__,
            "manifestSchemaVersion": manifest["schema_version"],
        },
        "manifestDigest": {"sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()},
        "command": list(manifest["command"]["argv"]),
        "execution": {
            "exitCode": manifest["execution"]["exit_code"],
            "durationSeconds": manifest["execution"]["duration_seconds"],
        },
        "materials": _subjects(manifest["inputs"], only_present=False),
        "environment": {
            "python": environment["python"],
            "platform": environment["platform"],
            "variables": env_vars,
            "packages": [
                {"name": d["name"], "version": d["version"]} for d in manifest["dependencies"]
            ],
            "recordedBy": environment.get("source"),
            "hardware": environment.get("hardware"),
        },
        "git": git,
        "note": manifest.get("note"),
        "outputs": [
            {
                "name": o["path"],
                "present": o["present"],
                "compare": {k: v for k, v in o["compare"].items() if k != "snapshot"},
            }
            for o in manifest["outputs"]
        ],
        "claims": [claim_text(c) for c in manifest.get("claims") or []],
        "notes": [
            "unsigned: this Statement is not an attestation until it is signed by the consumer",
            "hashes show integrity relative to the recorded digests, not that a result is correct",
        ],
    }
    return {
        "_type": STATEMENT_TYPE,
        "subject": _subjects(manifest["outputs"], only_present=True),
        "predicateType": PREDICATE_TYPE,
        "predicate": predicate,
    }


def to_dsse(manifest_path: Path | str) -> dict[str, Any]:
    """The in-toto Statement wrapped in a DSSE envelope ready for signing (e.g. Sigstore)."""
    statement = to_in_toto(manifest_path)
    raw = json.dumps(statement, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return {
        "payloadType": DSSE_PAYLOAD_TYPE,
        "payload": base64.b64encode(raw).decode("ascii"),
        "signatures": [],
    }


def to_openlineage(manifest_path: Path | str) -> list[dict[str, Any]]:
    """The run as two OpenLineage run events: START, then COMPLETE (exit code 0) or FAIL.

    The run id is a UUID derived from the SHA-256 of the manifest, so exporting the same manifest
    again gives the same events. The start time is the capture time minus the recorded duration
    (derived, not measured separately). Datasets are named by their path relative to the
    manifest's directory, in the namespace ``sciproof-files``; a directory is one dataset.
    """
    manifest_path = Path(manifest_path).resolve()
    manifest = load_manifest(manifest_path)
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sciproof:manifest:{digest}"))
    ended = datetime.fromisoformat(manifest["created_at"])
    try:
        started = ended - timedelta(seconds=manifest["execution"]["duration_seconds"])
    except OverflowError:
        raise UsageError(
            "openlineage: start time out of range (created_at"
            f" {manifest['created_at']!r} minus duration_seconds"
            f" {manifest['execution']['duration_seconds']!r})"
        ) from None

    def datasets(items: list[dict[str, Any]], *, only_present: bool) -> list[dict[str, str]]:
        return [
            {"namespace": OPENLINEAGE_DATASET_NAMESPACE, "name": item["path"]}
            for item in items
            if not only_present or item.get("present", True)
        ]

    inputs = datasets(manifest["inputs"], only_present=False)
    outputs = datasets(manifest["outputs"], only_present=True)
    succeeded = manifest["execution"]["exit_code"] == 0

    def event(event_type: str, when: datetime) -> dict[str, Any]:
        return {
            "eventType": event_type,
            "eventTime": when.isoformat(),
            "run": {"runId": run_id},
            "job": {"namespace": OPENLINEAGE_JOB_NAMESPACE, "name": manifest["name"]},
            "inputs": inputs,
            "outputs": outputs,
            "producer": OPENLINEAGE_PRODUCER,
            "schemaURL": OPENLINEAGE_SCHEMA_URL,
        }

    return [event("START", started), event("COMPLETE" if succeeded else "FAIL", ended)]


def format_openlineage(manifest_path: Path | str) -> str:
    return json.dumps(to_openlineage(manifest_path), indent=2, allow_nan=False) + "\n"


def format_in_toto(manifest_path: Path | str) -> str:
    return json.dumps(to_in_toto(manifest_path), indent=2, allow_nan=False) + "\n"


def format_dsse(manifest_path: Path | str) -> str:
    return json.dumps(to_dsse(manifest_path), indent=2, allow_nan=False) + "\n"


RO_CRATE_CONTEXT = "https://w3id.org/ro/crate/1.1/context"
PROCESS_RUN_CRATE_PROFILE = "https://w3id.org/ro/wfrun/process/0.6"


def to_ro_crate(manifest_path: Path | str) -> dict[str, Any]:
    """The manifest as a Workflow Run RO-Crate (Process Run Crate 0.6)."""
    manifest_path = Path(manifest_path).resolve()
    manifest = load_manifest(manifest_path)
    ended = datetime.fromisoformat(manifest["created_at"])
    duration = manifest["execution"]["duration_seconds"]
    try:
        started = ended - timedelta(seconds=duration)
    except OverflowError:
        started = ended

    exit_code = manifest["execution"]["exit_code"]
    succeeded = exit_code == 0

    root_dataset: dict[str, Any] = {
        "@id": "./",
        "@type": "Dataset",
        "conformsTo": [{"@id": PROCESS_RUN_CRATE_PROFILE}],
        "name": manifest["name"],
        "datePublished": manifest["created_at"],
        "hasPart": [],
        "mentions": [{"@id": "#run"}],
    }

    metadata_descriptor: dict[str, Any] = {
        "@id": "ro-crate-metadata.json",
        "@type": "CreativeWork",
        "conformsTo": [{"@id": "https://w3id.org/ro/crate/1.1"}],
        "about": {"@id": "./"},
    }

    run_action: dict[str, Any] = {
        "@id": "#run",
        "@type": "CreateAction",
        "name": manifest["name"],
        "description": " ".join(manifest["command"]["argv"]),
        "actionStatus": (
            "http://schema.org/CompletedActionStatus"
            if succeeded
            else "http://schema.org/FailedActionStatus"
        ),
        "startTime": started.isoformat(),
        "endTime": ended.isoformat(),
        "instrument": {"@id": "#tool"},
        "object": [],
        "result": [],
        "environment": [],
    }

    tool_entity: dict[str, Any] = {
        "@id": "#tool",
        "@type": "SoftwareApplication",
        "name": "sciproof",
        "version": __version__,
        "url": "https://github.com/hayagrivai-labs/sciproof",
    }

    graph: list[dict[str, Any]] = [
        root_dataset,
        metadata_descriptor,
        run_action,
        tool_entity,
    ]

    env_vars = dict(manifest.get("environment", {}).get("env_vars", {}))
    recorded_vars = manifest.get("environment", {}).get("recorded_env", {})
    if recorded_vars:
        for k, v in recorded_vars.items():
            if v is not None and k not in env_vars:
                env_vars[k] = v

    for key, val in env_vars.items():
        run_action["environment"].append(
            {
                "@type": "PropertyValue",
                "name": key,
                "value": val,
            }
        )

    for inp in manifest.get("inputs", []):
        path = inp["path"]
        is_dir = inp.get("kind") == "directory"
        entity_id = f"{path}/" if is_dir and not path.endswith("/") else path
        entity: dict[str, Any] = {
            "@id": entity_id,
            "@type": "Dataset" if is_dir else "File",
            "name": path,
            "sha256": inp.get("sha256"),
        }
        if "size" in inp:
            entity["contentSize"] = inp["size"]
        graph.append(entity)
        root_dataset["hasPart"].append({"@id": entity_id})
        run_action["object"].append({"@id": entity_id})

    for out in manifest.get("outputs", []):
        if not out.get("present", True):
            continue
        path = out["path"]
        is_dir = out.get("kind") == "directory"
        entity_id = f"{path}/" if is_dir and not path.endswith("/") else path
        entity = {
            "@id": entity_id,
            "@type": "Dataset" if is_dir else "File",
            "name": path,
            "sha256": out.get("sha256"),
        }
        if "size" in out:
            entity["contentSize"] = out["size"]
        graph.append(entity)
        root_dataset["hasPart"].append({"@id": entity_id})
        run_action["result"].append({"@id": entity_id})

    return {
        "@context": RO_CRATE_CONTEXT,
        "@graph": graph,
    }


def format_ro_crate(manifest_path: Path | str) -> str:
    return json.dumps(to_ro_crate(manifest_path), indent=2, allow_nan=False) + "\n"

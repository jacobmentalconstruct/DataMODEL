from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from . import storage, substrate
from .instance import InstanceContext


class AwarenessError(RuntimeError):
    pass


TABLES = ("awareness_revisions", "awareness_items")

# Compact projection bounds. Whenever a bound truncates, the revision states shown/total
# counts and a limitation line so the omitted remainder is explicit rather than silent.
_SOURCE_HANDLE_LIMIT = 100
_CLAIM_FINDING_LIMIT = 10
_RESOURCE_HANDLE_LIMIT = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _awareness_id() -> str:
    return f"awareness:{uuid.uuid4().hex}"


def _item_id() -> str:
    return f"awareness:item:{uuid.uuid4().hex}"


def _json(document: Any) -> str:
    return json.dumps(document, sort_keys=True)


def _decode_revision(row: sqlite3.Row) -> dict:
    revision = {key: row[key] for key in row.keys()}
    revision["summary"] = json.loads(revision.pop("summary_json"))
    revision["limitations"] = json.loads(revision.pop("limitations_json"))
    revision["unknowns"] = json.loads(revision.pop("unknowns_json"))
    revision["source_handles"] = json.loads(revision.pop("source_handles_json"))
    revision["basis"] = {
        "status": revision.pop("basis_status"),
        "id": _basis_id(revision["basis_signature"]),
        "signature": revision.pop("basis_signature"),
    }
    revision["freshness"] = "unknown"
    revision["findings"] = []
    return revision


def _decode_item(row: sqlite3.Row) -> dict:
    item = {key: row[key] for key in row.keys()}
    item["source_handles"] = json.loads(item.pop("source_handles_json"))
    item["provenance"] = json.loads(item.pop("provenance_json"))
    return item


def _basis_id(signature: str | None) -> str | None:
    if signature is None:
        return None
    return f"basis:{signature[:32]}"


def _freshness(revision: dict, context: InstanceContext) -> str:
    stored = revision.get("target_signature")
    if not stored:
        return "unknown"
    current = substrate.target_signature(context)
    return "current" if current == stored else "stale"


def status(context: InstanceContext) -> dict:
    connection = storage.connect(context)
    try:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES
        }
    finally:
        connection.close()
    return {"ok": True, "counts": counts}


def refresh(context: InstanceContext) -> dict:
    created_at = _now()
    basis_view = substrate.current_awareness_basis(context)
    basis_missing = basis_view["status"] == "missing"

    if basis_missing:
        basis_status = "missing"
        basis = None
        source_handles: list[str] = []
        summary = {
            "target_state": "unknown_unobserved",
            "domain_profile": "unknown",
            "resource_count": None,
            "claim_count": 0,
        }
        findings: list[dict] = []
        limitations = ["no substrate observations exist; awareness basis is unknown"]
        unknowns = ["target content has not been observed by substrate; unobserved remains unknown"]
    else:
        basis_status = "observed"
        basis = basis_view["basis_signature"]
        resources = basis_view["resources"]
        claims = basis_view["claims"]
        all_source_handles = basis_view["source_handles"]
        source_handles = all_source_handles[:_SOURCE_HANDLE_LIMIT]
        empty_claim = next((item for item in claims if item["claim_type"] == "target_empty"), None)
        target_state = "observed_empty" if empty_claim else "observed_non_empty"
        findings = _findings_from_substrate(resources, claims)
        projection = {
            "source_handles": {"shown": len(source_handles), "total": len(all_source_handles)},
            "claim_findings": {
                "shown": min(len(claims), _CLAIM_FINDING_LIMIT),
                "total": len(claims),
            },
            "resource_handles": {
                "shown": min(len(resources), _RESOURCE_HANDLE_LIMIT),
                "total": len(resources),
            },
        }
        summary = {
            "target_state": target_state,
            "domain_profile": _domain_profile(claims, empty=empty_claim is not None),
            "resource_count": len(resources),
            "claim_count": len(claims),
            "projection": projection,
        }
        limitations = _observed_limitations(
            resources,
            claims,
            inventory_limitations=_inventory_limitations(basis_view["observations"]),
            projection=projection,
        )
        unknowns = ["anything not represented in substrate observations remains unknown"]

    awareness_id = _awareness_id()
    revision = {
        "awareness_id": awareness_id,
        "created_at": created_at,
        "basis": {"status": basis_status, "id": _basis_id(basis), "signature": basis},
        "target_signature": basis_view["target_signature"] if basis else None,
        "freshness": "unknown",
        "summary": summary,
        "limitations": limitations,
        "unknowns": unknowns,
        "source_handles": source_handles,
        "findings": findings,
    }
    revision["freshness"] = _freshness(revision, context)

    connection = storage.connect(context)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO awareness_revisions
                    (awareness_id, created_at, basis_status, basis_signature,
                     target_signature, summary_json, limitations_json, unknowns_json,
                     source_handles_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    awareness_id,
                    created_at,
                    basis_status,
                    basis,
                    basis_view["target_signature"] if basis else None,
                    _json(summary),
                    _json(limitations),
                    _json(unknowns),
                    _json(source_handles),
                ),
            )
            for item in findings:
                connection.execute(
                    """
                    INSERT INTO awareness_items
                        (item_id, awareness_id, item_type, title, statement, priority,
                         source_handles_json, provenance_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["item_id"],
                        awareness_id,
                        item["item_type"],
                        item["title"],
                        item["statement"],
                        item["priority"],
                        _json(item["source_handles"]),
                        _json(item["provenance"]),
                    ),
                )
    finally:
        connection.close()
    return {"ok": True, "revision": revision}


def current(context: InstanceContext) -> dict:
    connection = storage.connect(context)
    try:
        row = connection.execute(
            """
            SELECT awareness_id, created_at, basis_status, basis_signature,
                   target_signature, summary_json, limitations_json, unknowns_json,
                   source_handles_json
            FROM awareness_revisions
            ORDER BY rowid DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise AwarenessError("no awareness revision exists")
    revision = _read_revision_row(context, row)
    return {"ok": True, "revision": revision}


def list_revisions(context: InstanceContext, limit: int = 50) -> list[dict]:
    connection = storage.connect(context)
    try:
        rows = connection.execute(
            """
            SELECT awareness_id, created_at, basis_status, basis_signature,
                   target_signature, summary_json, limitations_json, unknowns_json,
                   source_handles_json
            FROM awareness_revisions
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (_bounded_limit(limit),),
        ).fetchall()
    finally:
        connection.close()
    return [_read_revision_row(context, row, include_findings=False) for row in rows]


def read_revision(context: InstanceContext, awareness_id: str) -> dict:
    connection = storage.connect(context)
    try:
        row = connection.execute(
            """
            SELECT awareness_id, created_at, basis_status, basis_signature,
                   target_signature, summary_json, limitations_json, unknowns_json,
                   source_handles_json
            FROM awareness_revisions
            WHERE awareness_id = ?
            """,
            (awareness_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise AwarenessError(f"awareness revision not found: {awareness_id}")
    return _read_revision_row(context, row)


def drill(context: InstanceContext, item_id: str) -> dict:
    connection = storage.connect(context)
    try:
        row = connection.execute(
            """
            SELECT item_id, awareness_id, item_type, title, statement, priority,
                   source_handles_json, provenance_json
            FROM awareness_items
            WHERE item_id = ?
            """,
            (item_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise AwarenessError(f"awareness item not found: {item_id}")

    item = _decode_item(row)
    nodes: dict[str, dict] = {}
    relations: list[dict] = []
    for handle in item["source_handles"]:
        if handle.startswith("claim:"):
            try:
                trace = substrate.trace(context, handle)
            except substrate.SubstrateError:
                # Claims are re-derived every snapshot; older ones are pruned.
                nodes[handle] = {"id": handle, "type": "claim", "status": "superseded by a later snapshot"}
                continue
            for node in trace["nodes"]:
                nodes[node["id"]] = node
            relations.extend(trace["relations"])
        elif handle.startswith("path:"):
            resource = substrate.read_resource(context, handle)
            nodes[resource["resource_id"]] = {
                "id": resource["resource_id"],
                "type": "resource",
                "handle": resource["handle"],
                "path": resource["path"],
                "kind": resource["kind"],
            }
        elif handle.startswith("evidence:"):
            evidence = substrate.read_evidence(context, handle)
            nodes[evidence["evidence_id"]] = {
                "id": evidence["evidence_id"],
                "type": "evidence",
                "kind": evidence["kind"],
                "digest": evidence["digest"],
            }
    return {"item": item, "nodes": list(nodes.values()), "relations": relations}


def _read_revision_row(
    context: InstanceContext,
    row: sqlite3.Row,
    *,
    include_findings: bool = True,
) -> dict:
    revision = _decode_revision(row)
    revision["freshness"] = _freshness(revision, context)
    if include_findings:
        connection = storage.connect(context)
        try:
            rows = connection.execute(
                """
                SELECT item_id, awareness_id, item_type, title, statement, priority,
                       source_handles_json, provenance_json
                FROM awareness_items
                WHERE awareness_id = ?
                ORDER BY rowid
                """,
                (revision["awareness_id"],),
            ).fetchall()
        finally:
            connection.close()
        revision["findings"] = [_decode_item(item) for item in rows]
    return revision


def _findings_from_substrate(resources: list[dict], claims: list[dict]) -> list[dict]:
    findings: list[dict] = []
    for claim in claims[:_CLAIM_FINDING_LIMIT]:
        source_handles = [claim["claim_id"]]
        findings.append(
            {
                "item_id": _item_id(),
                "item_type": "derived_claim",
                "title": claim["claim_type"],
                "statement": claim["statement"],
                "priority": 10,
                "source_handles": source_handles,
                "provenance": {"source": "substrate.claim", "handles": source_handles},
            }
        )
    if resources:
        handles = [item["handle"] for item in resources[:_RESOURCE_HANDLE_LIMIT]]
        findings.append(
            {
                "item_id": _item_id(),
                "item_type": "resource_orientation",
                "title": "observed_resources",
                "statement": f"{len(resources)} target resources are present in the substrate",
                "priority": 20,
                "source_handles": handles,
                "provenance": {"source": "substrate.resources", "handles": handles},
            }
        )
    return findings


def _domain_profile(claims: list[dict], *, empty: bool) -> str:
    if empty:
        return "empty_or_nascent"
    claim_types = {claim["claim_type"] for claim in claims}
    has_software = "target_profile_software" in claim_types
    has_records_documents = "target_profile_records_documents" in claim_types
    if has_software and has_records_documents:
        return "mixed"
    if has_software:
        return "software"
    if has_records_documents:
        return "records_documents"
    return "generic_observed"


def _inventory_limitations(observations: list[dict]) -> list[str]:
    limitations: list[str] = []
    for observation in observations:
        if observation.get("observation_type") != "resource_inventory":
            continue
        for limit in observation.get("data", {}).get("limitations", []):
            if limit not in limitations:
                limitations.append(limit)
    return limitations


def _truncation_limitations(projection: dict) -> list[str]:
    limitations: list[str] = []
    labels = {
        "source_handles": "source handles",
        "claim_findings": "claim findings",
        "resource_handles": "resource handles in the orientation finding",
    }
    for key, label in labels.items():
        counts = projection.get(key, {})
        shown = counts.get("shown", 0)
        total = counts.get("total", 0)
        if total > shown:
            limitations.append(
                f"awareness lists {shown} of {total} {label}; the remaining {total - shown}"
                " are omitted from this compact projection and stay reachable through the"
                " substrate basis"
            )
    return limitations


def _observed_limitations(
    resources: list[dict],
    claims: list[dict],
    *,
    inventory_limitations: list[str] | None = None,
    projection: dict | None = None,
) -> list[str]:
    limitations = [
        "awareness is a compact projection over the latest substrate refresh, not a complete target scan",
    ]
    limitations.extend(_truncation_limitations(projection or {}))
    for limit in inventory_limitations or []:
        if limit not in limitations:
            limitations.append(limit)
    if any(claim["claim_type"].startswith("target_profile_") for claim in claims):
        limitations.append("domain profile is derived from deterministic substrate signals only")
    else:
        limitations.append(
            "domain-specific contributors have not run; orientation is limited to generic substrate records"
        )
    for claim in claims:
        if claim["claim_type"] == "target_has_weak_material":
            limitations.append("weak material is represented with metadata-only or limited-basis evidence")
        for limit in claim.get("data", {}).get("limitations", []):
            if limit not in limitations:
                limitations.append(limit)
    if not resources:
        limitations.append("substrate observed no target resources, so awareness is intentionally thin")
    return limitations


def _bounded_limit(limit: int) -> int:
    return max(1, min(int(limit), 500))

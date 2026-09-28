from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import app_journal, awareness, storage, substrate
from .containment import ContainmentError, resolve_declared_paths
from .contracts import validate_json
from .control import ControlPlane
from .instance import InstanceContext
from .registry import get as get_manifest


class MutationError(RuntimeError):
    pass


TABLES = (
    "mutation_previews",
    "mutation_approvals",
    "mutation_records",
    "mutation_verifications",
    "mutation_links",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(document: Any) -> str:
    return json.dumps(document, sort_keys=True)


def _digest(document: Any) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _id(prefix: str) -> str:
    return f"mutation:{prefix}:{uuid.uuid4().hex}"


def _content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise MutationError("write preview path is not a file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def preview_write(
    context: InstanceContext,
    *,
    path: str,
    content: str,
    overwrite: bool = False,
) -> dict:
    manifest = get_manifest(context, "write")
    arguments = {
        "path": path,
        "content": content,
        "overwrite": bool(overwrite),
    }
    errors = validate_json(arguments, manifest.input_schema)
    if errors:
        raise MutationError("; ".join(errors))
    try:
        resolved = resolve_declared_paths(context, manifest, arguments)
    except ContainmentError as exc:
        raise MutationError(str(exc)) from exc

    target_path = Path(resolved["path"])
    relative_path = target_path.relative_to(context.target_root).as_posix()
    current_awareness = awareness.current(context)["revision"]
    if current_awareness["freshness"] != "current":
        raise MutationError("current awareness is not fresh enough to preview mutation")
    basis_signature = current_awareness["basis"]["signature"]
    target_signature = current_awareness["target_signature"]
    before_exists = target_path.exists()
    before_digest = _file_digest(target_path)
    after_digest = _content_digest(content)
    content_digest = after_digest
    expected_changed_paths = [relative_path]
    payload = {
        "operation": "write_file",
        "path": relative_path,
        "content": content,
        "content_digest": content_digest,
        "before_exists": before_exists,
        "before_digest": before_digest,
        "after_digest": after_digest,
        "overwrite": bool(overwrite),
        "expected_changed_paths": expected_changed_paths,
        "awareness_id": current_awareness["awareness_id"],
        "basis_signature": basis_signature,
        "target_signature": target_signature,
        "instance_uuid": context.instance_uuid,
    }
    preview_digest = _digest(payload)
    preview_id = _id("preview")
    created_at = _now()
    connection = storage.connect(context)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO mutation_previews
                    (preview_id, created_at, instance_uuid, operation, path,
                     content_digest, before_exists, before_digest, after_digest,
                     overwrite, expected_changed_paths_json, awareness_id,
                     basis_signature, target_signature, preview_digest, payload_json,
                     status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    preview_id,
                    created_at,
                    context.instance_uuid,
                    "write_file",
                    relative_path,
                    content_digest,
                    int(before_exists),
                    before_digest,
                    after_digest,
                    int(bool(overwrite)),
                    _json(expected_changed_paths),
                    current_awareness["awareness_id"],
                    basis_signature,
                    target_signature,
                    preview_digest,
                    _json(payload),
                    "previewed",
                ),
            )
    finally:
        connection.close()
    return {"ok": True, "preview": _preview_document(preview_id, created_at, payload, preview_digest)}


def approve(
    context: InstanceContext,
    preview_id: str,
    *,
    journal_entry_id: str | None = None,
) -> dict:
    preview = read_preview(context, preview_id)
    if preview["status"] != "previewed":
        raise MutationError(f"preview is not approvable: {preview_id}")
    if journal_entry_id:
        app_journal.read_entry(context, journal_entry_id)

    approval_id = _id("approval")
    created_at = _now()
    connection = storage.connect(context)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO mutation_approvals
                    (approval_id, created_at, instance_uuid, preview_id,
                     preview_digest, basis_signature, target_signature, status,
                     journal_entry_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    created_at,
                    context.instance_uuid,
                    preview_id,
                    preview["preview_digest"],
                    preview["basis_signature"],
                    preview["target_signature"],
                    "approved",
                    journal_entry_id,
                ),
            )
            if journal_entry_id:
                _insert_link(
                    connection,
                    source_id=preview_id,
                    target_type="journal",
                    target_id=journal_entry_id,
                    created_at=created_at,
                )
                _insert_link(
                    connection,
                    source_id=approval_id,
                    target_type="journal",
                    target_id=journal_entry_id,
                    created_at=created_at,
                )
    finally:
        connection.close()
    return {
        "ok": True,
        "approval": {
            "approval_id": approval_id,
            "created_at": created_at,
            "preview_id": preview_id,
            "preview_digest": preview["preview_digest"],
            "basis_signature": preview["basis_signature"],
            "target_signature": preview["target_signature"],
            "status": "approved",
            "journal_entry_id": journal_entry_id,
        },
    }


def apply(
    context: InstanceContext,
    approval_id: str,
    *,
    preview_id: str | None = None,
) -> dict:
    approval = _read_approval(context, approval_id)
    if approval is None:
        return _refusal(
            context,
            preview_id=preview_id,
            approval_id=None,
            code="approval_not_found",
            detail=f"approval not found: {approval_id}",
        )
    if preview_id and preview_id != approval["preview_id"]:
        return _refusal(
            context,
            preview_id=preview_id,
            approval_id=approval_id,
            code="approval_preview_mismatch",
            detail="approval is bound to a different preview",
        )

    preview = read_preview(context, approval["preview_id"])
    if approval["preview_digest"] != preview["preview_digest"]:
        return _refusal(
            context,
            preview_id=preview["preview_id"],
            approval_id=approval_id,
            code="approval_preview_mismatch",
            detail="approval digest no longer matches preview",
        )

    current_signature = substrate.target_signature(context)
    if current_signature != preview["target_signature"]:
        return _refusal(
            context,
            preview_id=preview["preview_id"],
            approval_id=approval_id,
            code="stale_target",
            detail="target signature differs from reviewed preview",
        )
    try:
        current_awareness = awareness.current(context)["revision"]
    except awareness.AwarenessError as exc:
        return _refusal(
            context,
            preview_id=preview["preview_id"],
            approval_id=approval_id,
            code="stale_basis",
            detail=str(exc),
        )
    if (
        current_awareness["awareness_id"] != preview["awareness_id"]
        or current_awareness["basis"]["signature"] != preview["basis_signature"]
        or current_awareness["freshness"] != "current"
    ):
        return _refusal(
            context,
            preview_id=preview["preview_id"],
            approval_id=approval_id,
            code="stale_basis",
            detail="current awareness basis differs from reviewed preview",
        )

    before = _target_snapshot(context)
    payload = preview["payload"]
    response = ControlPlane(context).invoke(
        "write",
        {
            "path": payload["path"],
            "content": payload["content"],
            "overwrite": bool(payload["overwrite"]),
        },
        client="mutation",
        authority="apply",
    )
    after = _target_snapshot(context)
    measurement = _measure_changed_paths(before, after)
    verification = _record_verification(
        context,
        status="unavailable",
        method="target_native_detection",
        detail={
            "detail": "No target-native verification mechanism is available.",
            "checked": [],
        },
    )
    if not response.get("ok"):
        return _record_apply(
            context,
            preview=preview,
            approval=approval,
            status="failed",
            refusal_code=response.get("error", {}).get("code", "tool_failed"),
            receipt_id=response.get("receipt_id"),
            artifact_id=response.get("artifact_id"),
            measurement=measurement,
            verification=verification,
            pre_awareness_id=current_awareness["awareness_id"],
            post_awareness_id=None,
            substrate_refresh={},
            detail={"tool_response": response},
        )

    substrate_refresh = substrate.refresh(context)
    post_awareness = awareness.refresh(context)["revision"]
    return _record_apply(
        context,
        preview=preview,
        approval=approval,
        status="applied",
        refusal_code=None,
        receipt_id=response.get("receipt_id"),
        artifact_id=response.get("artifact_id"),
        measurement=measurement,
        verification=verification,
        pre_awareness_id=current_awareness["awareness_id"],
        post_awareness_id=post_awareness["awareness_id"],
        substrate_refresh=substrate_refresh,
        detail={
            "tool_response": {
                "ok": response.get("ok"),
                "tool_id": response.get("tool_id"),
                "result_handle": response.get("result", {}).get("handle"),
            },
            "post_basis": post_awareness["basis"],
        },
    )


def list_history(context: InstanceContext, limit: int = 50) -> list[dict]:
    connection = storage.connect(context)
    try:
        rows = connection.execute(
            """
            SELECT mutation_id, created_at, preview_id, approval_id, status,
                   refusal_code, receipt_id, artifact_id, measurement_json,
                   verification_id, pre_awareness_id, post_awareness_id,
                   substrate_refresh_json, detail_json
            FROM mutation_records
            ORDER BY rowid
            LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    finally:
        connection.close()
    return [_decode_record(row) for row in rows]


def read_preview(context: InstanceContext, preview_id: str) -> dict:
    connection = storage.connect(context)
    try:
        row = connection.execute(
            """
            SELECT preview_id, created_at, operation, path, content_digest,
                   before_exists, before_digest, after_digest, overwrite,
                   expected_changed_paths_json, awareness_id, basis_signature,
                   target_signature, preview_digest, payload_json, status
            FROM mutation_previews
            WHERE preview_id = ?
            """,
            (preview_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise MutationError(f"preview not found: {preview_id}")
    return _decode_preview(row)


def links(context: InstanceContext, source_id: str) -> list[dict]:
    connection = storage.connect(context)
    try:
        rows = connection.execute(
            """
            SELECT link_id, created_at, source_id, target_type, target_id
            FROM mutation_links
            WHERE source_id = ?
            ORDER BY link_id
            """,
            (source_id,),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "link_id": f"mutation:link:{row['link_id']}",
            "created_at": row["created_at"],
            "source_id": row["source_id"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
        }
        for row in rows
    ]


def _read_approval(context: InstanceContext, approval_id: str) -> dict | None:
    connection = storage.connect(context)
    try:
        row = connection.execute(
            """
            SELECT approval_id, created_at, preview_id, preview_digest,
                   basis_signature, target_signature, status, journal_entry_id
            FROM mutation_approvals
            WHERE approval_id = ?
            """,
            (approval_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _decode_preview(row: sqlite3.Row) -> dict:
    return {
        "preview_id": row["preview_id"],
        "created_at": row["created_at"],
        "operation": row["operation"],
        "path": row["path"],
        "content_digest": row["content_digest"],
        "before_exists": bool(row["before_exists"]),
        "before_digest": row["before_digest"],
        "after_digest": row["after_digest"],
        "overwrite": bool(row["overwrite"]),
        "expected_changed_paths": json.loads(row["expected_changed_paths_json"]),
        "awareness_id": row["awareness_id"],
        "basis_signature": row["basis_signature"],
        "target_signature": row["target_signature"],
        "preview_digest": row["preview_digest"],
        "payload": json.loads(row["payload_json"]),
        "status": row["status"],
    }


def _preview_document(
    preview_id: str | None,
    created_at: str,
    payload: dict,
    preview_digest: str,
) -> dict:
    return {
        "preview_id": preview_id,
        "created_at": created_at,
        "operation": payload["operation"],
        "path": payload["path"],
        "content_digest": payload["content_digest"],
        "before_exists": payload["before_exists"],
        "before_digest": payload["before_digest"],
        "after_digest": payload["after_digest"],
        "overwrite": payload["overwrite"],
        "expected_changed_paths": payload["expected_changed_paths"],
        "awareness_id": payload["awareness_id"],
        "basis_signature": payload["basis_signature"],
        "target_signature": payload["target_signature"],
        "preview_digest": preview_digest,
        "status": "previewed",
    }


def _record_verification(
    context: InstanceContext,
    *,
    status: str,
    method: str,
    detail: dict,
) -> dict:
    verification_id = _id("verification")
    created_at = _now()
    connection = storage.connect(context)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO mutation_verifications
                    (verification_id, created_at, status, method, detail_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (verification_id, created_at, status, method, _json(detail)),
            )
    finally:
        connection.close()
    return {
        "verification_id": verification_id,
        "created_at": created_at,
        "status": status,
        "method": method,
        "detail": detail["detail"],
        "evidence": detail,
    }


def _record_apply(
    context: InstanceContext,
    *,
    preview: dict,
    approval: dict,
    status: str,
    refusal_code: str | None,
    receipt_id: str | None,
    artifact_id: str | None,
    measurement: dict,
    verification: dict,
    pre_awareness_id: str | None,
    post_awareness_id: str | None,
    substrate_refresh: dict,
    detail: dict,
) -> dict:
    mutation_id = _id("record")
    created_at = _now()
    record_detail = {
        **detail,
        "preview_digest": preview["preview_digest"],
        "reviewed_target_signature": preview["target_signature"],
        "reviewed_basis_signature": preview["basis_signature"],
    }
    connection = storage.connect(context)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO mutation_records
                    (mutation_id, created_at, instance_uuid, preview_id, approval_id,
                     status, refusal_code, receipt_id, artifact_id, measurement_json,
                     verification_id, pre_awareness_id, post_awareness_id,
                     substrate_refresh_json, detail_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mutation_id,
                    created_at,
                    context.instance_uuid,
                    preview["preview_id"],
                    approval["approval_id"],
                    status,
                    refusal_code,
                    receipt_id,
                    artifact_id,
                    _json(measurement),
                    verification["verification_id"],
                    pre_awareness_id,
                    post_awareness_id,
                    _json(substrate_refresh),
                    _json(record_detail),
                ),
            )
            _insert_record_links(
                connection,
                created_at=created_at,
                mutation_id=mutation_id,
                preview=preview,
                approval=approval,
                receipt_id=receipt_id,
                artifact_id=artifact_id,
                verification_id=verification["verification_id"],
                pre_awareness_id=pre_awareness_id,
                post_awareness_id=post_awareness_id,
            )
    finally:
        connection.close()
    record = {
        "mutation_id": mutation_id,
        "created_at": created_at,
        "preview_id": preview["preview_id"],
        "approval_id": approval["approval_id"],
        "status": status,
        "refusal_code": refusal_code,
        "receipt_id": receipt_id,
        "artifact_id": artifact_id,
        "measurement": measurement,
        "verification": verification,
        "pre_awareness_id": pre_awareness_id,
        "post_awareness_id": post_awareness_id,
        "substrate_refresh": substrate_refresh,
        "detail": record_detail,
    }
    return {"ok": status == "applied", "mutation": record} if status == "applied" else {
        "ok": False,
        "mutation": record,
        "error": {"code": refusal_code or "mutation_failed", "message": str(record_detail)},
    }


def _refusal(
    context: InstanceContext,
    *,
    preview_id: str,
    approval_id: str | None,
    code: str,
    detail: str,
) -> dict:
    mutation_id = _id("record")
    created_at = _now()
    connection = storage.connect(context)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO mutation_records
                    (mutation_id, created_at, instance_uuid, preview_id, approval_id,
                     status, refusal_code, receipt_id, artifact_id, measurement_json,
                     verification_id, pre_awareness_id, post_awareness_id,
                     substrate_refresh_json, detail_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mutation_id,
                    created_at,
                    context.instance_uuid,
                    preview_id,
                    approval_id,
                    "refused",
                    code,
                    None,
                    None,
                    _json({"changed_paths": [], "source": "not_launched"}),
                    None,
                    None,
                    None,
                    _json({}),
                    _json({"detail": detail}),
                ),
            )
    finally:
        connection.close()
    return {
        "ok": False,
        "error": {"code": code, "message": detail},
        "mutation": {
            "mutation_id": mutation_id,
            "created_at": created_at,
            "preview_id": preview_id,
            "approval_id": approval_id,
            "status": "refused",
            "refusal_code": code,
            "measurement": {"changed_paths": [], "source": "not_launched"},
        },
    }


def _insert_record_links(
    connection: sqlite3.Connection,
    *,
    created_at: str,
    mutation_id: str,
    preview: dict,
    approval: dict,
    receipt_id: str | None,
    artifact_id: str | None,
    verification_id: str,
    pre_awareness_id: str | None,
    post_awareness_id: str | None,
) -> None:
    links = [
        ("preview", preview["preview_id"]),
        ("approval", approval["approval_id"]),
        ("verification", verification_id),
    ]
    if receipt_id:
        links.append(("operation", receipt_id))
    if artifact_id:
        links.append(("artifact", artifact_id))
    if pre_awareness_id:
        links.append(("awareness", pre_awareness_id))
    if post_awareness_id:
        links.append(("awareness", post_awareness_id))
    if approval.get("journal_entry_id"):
        links.append(("journal", approval["journal_entry_id"]))
    for target_type, target_id in links:
        _insert_link(
            connection,
            source_id=mutation_id,
            target_type=target_type,
            target_id=target_id,
            created_at=created_at,
        )


def _insert_link(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    target_type: str,
    target_id: str,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO mutation_links (created_at, source_id, target_type, target_id)
        VALUES (?, ?, ?, ?)
        """,
        (created_at, source_id, target_type, target_id),
    )


def _target_snapshot(context: InstanceContext) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    instance_root = context.instance_root.resolve()
    for path in sorted(context.target_root.rglob("*")):
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(instance_root)
            continue
        except ValueError:
            pass
        relative = path.relative_to(context.target_root).as_posix()
        if path.is_file():
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _measure_changed_paths(before: dict[str, str], after: dict[str, str]) -> dict:
    changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    return {
        "changed_paths": changed,
        "source": "independent_target_snapshot",
    }


def _decode_record(row: sqlite3.Row) -> dict:
    return {
        "mutation_id": row["mutation_id"],
        "created_at": row["created_at"],
        "preview_id": row["preview_id"],
        "approval_id": row["approval_id"],
        "status": row["status"],
        "refusal_code": row["refusal_code"],
        "receipt_id": row["receipt_id"],
        "artifact_id": row["artifact_id"],
        "measurement": json.loads(row["measurement_json"]),
        "verification_id": row["verification_id"],
        "pre_awareness_id": row["pre_awareness_id"],
        "post_awareness_id": row["post_awareness_id"],
        "substrate_refresh": json.loads(row["substrate_refresh_json"]),
        "detail": json.loads(row["detail_json"]),
    }

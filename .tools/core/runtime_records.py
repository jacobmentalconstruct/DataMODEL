from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
import zlib
from datetime import datetime, timezone
from typing import Any

from . import storage
from .instance import InstanceContext


class RecordError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _artifact_id(digest: str) -> str:
    return f"artifact:{digest[:32]}"


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def _json_bytes(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def create_artifact(context: InstanceContext, kind: str, body: dict) -> dict:
    payload = _json_bytes(body)
    digest = hashlib.sha256(payload).hexdigest()
    artifact_id = _artifact_id(digest)
    try:
        connection = storage.connect(context)
        try:
            with connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO operational_artifacts
                        (artifact_id, created_at, kind, media_type, digest, body_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        _now(),
                        kind,
                        "application/json",
                        digest,
                        zlib.compress(payload, 6),  # stored compressed; digest is of the JSON
                    ),
                )
        finally:
            connection.close()
    except (OSError, sqlite3.Error, storage.StorageError) as exc:
        raise RecordError(f"could not persist operational artifact: {exc}") from exc
    return {
        "artifact_id": artifact_id,
        "kind": kind,
        "media_type": "application/json",
        "digest": digest,
        "body": body,
    }


def begin_receipt(
    context: InstanceContext,
    *,
    tool_id: str,
    client: str,
    authority: str,
) -> str:
    receipt_id = f"operation:{uuid.uuid4().hex}"
    try:
        connection = storage.connect(context)
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO operation_receipts
                        (receipt_id, instance_uuid, started_at, client, tool_id, authority, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt_id,
                        context.instance_uuid,
                        _now(),
                        client,
                        tool_id,
                        authority,
                        "started",
                    ),
                )
        finally:
            connection.close()
    except (OSError, sqlite3.Error, storage.StorageError) as exc:
        raise RecordError(f"could not establish operation receipt: {exc}") from exc
    return receipt_id


def complete_receipt(
    context: InstanceContext,
    receipt_id: str,
    *,
    status: str,
    envelope: dict,
    error_code: str | None = None,
    result_ok: bool | None = None,
    exit_code: int | None = None,
    duration_ms: int | None = None,
    manifest_digest: str | None = None,
    process: dict | None = None,
) -> str:
    artifact_body = {
        "envelope": envelope,
        "process": process or {},
    }
    payload = _json_bytes(artifact_body)
    digest = hashlib.sha256(payload).hexdigest()
    artifact_id = _artifact_id(digest)
    try:
        connection = storage.connect(context)
        try:
            with connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO operational_artifacts
                        (artifact_id, created_at, kind, media_type, digest, body_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        _now(),
                        "tool_result",
                        "application/json",
                        digest,
                        zlib.compress(payload, 6),  # stored compressed; digest is of the JSON
                    ),
                )
                updated = connection.execute(
                    """
                    UPDATE operation_receipts
                    SET completed_at = ?,
                        status = ?,
                        error_code = ?,
                        result_ok = ?,
                        exit_code = ?,
                        duration_ms = ?,
                        manifest_digest = ?,
                        artifact_id = ?
                    WHERE receipt_id = ?
                    """,
                    (
                        _now(),
                        status,
                        error_code,
                        None if result_ok is None else int(result_ok),
                        exit_code,
                        duration_ms,
                        manifest_digest,
                        artifact_id,
                        receipt_id,
                    ),
                ).rowcount
                if updated != 1:
                    raise RecordError(f"operation receipt not found: {receipt_id}")
        finally:
            connection.close()
    except (OSError, sqlite3.Error, storage.StorageError) as exc:
        raise RecordError(f"could not complete operation receipt: {exc}") from exc
    return artifact_id


def list_receipts(context: InstanceContext, limit: int = 50) -> list[dict]:
    connection = storage.connect(context)
    try:
        rows = connection.execute(
            """
            SELECT receipt_id, started_at, completed_at, client, tool_id, authority, status,
                   error_code, result_ok, exit_code, duration_ms, manifest_digest, artifact_id
            FROM operation_receipts
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    finally:
        connection.close()
    return [_normalize_receipt(_row_to_dict(row)) for row in rows]


def read_receipt(context: InstanceContext, receipt_id: str) -> dict:
    connection = storage.connect(context)
    try:
        row = connection.execute(
            """
            SELECT receipt_id, started_at, completed_at, client, tool_id, authority, status,
                   error_code, result_ok, exit_code, duration_ms, manifest_digest, artifact_id
            FROM operation_receipts
            WHERE receipt_id = ?
            """,
            (receipt_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RecordError(f"operation receipt not found: {receipt_id}")
    return _normalize_receipt(_row_to_dict(row))


def list_artifacts(context: InstanceContext, limit: int = 50) -> list[dict]:
    connection = storage.connect(context)
    try:
        rows = connection.execute(
            """
            SELECT artifact_id, created_at, kind, media_type, digest
            FROM operational_artifacts
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    finally:
        connection.close()
    return [_row_to_dict(row) for row in rows]


def read_artifact(context: InstanceContext, artifact_id: str) -> dict:
    connection = storage.connect(context)
    try:
        row = connection.execute(
            """
            SELECT artifact_id, created_at, kind, media_type, digest, body_json
            FROM operational_artifacts
            WHERE artifact_id = ?
            """,
            (artifact_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RecordError(f"operational artifact not found: {artifact_id}")
    document = _row_to_dict(row)
    body = document.pop("body_json")
    if isinstance(body, bytes):  # current rows are compressed; older rows are JSON text
        body = zlib.decompress(body).decode("utf-8")
    document["body"] = json.loads(body)
    return document


def _normalize_receipt(receipt: dict[str, Any]) -> dict:
    if receipt.get("result_ok") is not None:
        receipt["result_ok"] = bool(receipt["result_ok"])
    return receipt

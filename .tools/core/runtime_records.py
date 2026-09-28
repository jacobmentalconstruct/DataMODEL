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


# Retention (.dev/PLAN.md decision 4). Receipt rows are kept forever. Full artifacts are
# kept for calls that could change something (sandbox/apply tools that ran, kind
# "tool_result") and for anything a journal entry links to, directly or through its
# receipt. Results of observe-only calls and of refusals (kind "observation") are
# evidence of reading, not of change: only the newest OBSERVATIONS_KEPT are kept, pruned
# in batches once PRUNE_SLACK more have accumulated.
OBSERVATIONS_KEPT = 200
PRUNE_SLACK = 50
PRUNED_NOTE = f"pruned under retention (the newest {OBSERVATIONS_KEPT} observe results are kept)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _artifact_id(digest: str) -> str:
    return f"artifact:{digest[:32]}"


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def _json_bytes(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
    observation: bool = False,
) -> str:
    """Complete a receipt and store its artifact. `observation` marks the result of an
    observe-only call or a refusal: kept only among the newest OBSERVATIONS_KEPT."""
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
                        "observation" if observation else "tool_result",
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
            if observation:
                _prune_observations(connection)
        finally:
            connection.close()
    except (OSError, sqlite3.Error, storage.StorageError) as exc:
        raise RecordError(f"could not complete operation receipt: {exc}") from exc
    return artifact_id


def _prune_observations(connection: sqlite3.Connection) -> None:
    """Drop observation artifacts beyond the newest OBSERVATIONS_KEPT once PRUNE_SLACK more
    have accumulated, sparing journal-linked ones; their receipts keep their rows."""
    count = connection.execute(
        "SELECT COUNT(*) FROM operational_artifacts WHERE kind = 'observation'").fetchone()[0]
    if count <= OBSERVATIONS_KEPT + PRUNE_SLACK:
        return
    with connection:
        connection.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS pruned (artifact_id TEXT PRIMARY KEY)
            """
        )
        connection.execute("DELETE FROM temp.pruned")
        connection.execute(
            """
            INSERT INTO temp.pruned
            SELECT artifact_id FROM operational_artifacts
            WHERE kind = 'observation'
              AND artifact_id NOT IN (
                  SELECT target_id FROM app_journal_links WHERE target_type = 'artifact')
              AND artifact_id NOT IN (
                  SELECT r.artifact_id FROM operation_receipts AS r
                  JOIN app_journal_links AS l
                    ON l.target_type = 'operation' AND l.target_id = r.receipt_id
                  WHERE r.artifact_id IS NOT NULL)
            ORDER BY rowid DESC
            LIMIT -1 OFFSET ?
            """,
            (OBSERVATIONS_KEPT,),
        )
        connection.execute(
            "UPDATE operation_receipts SET artifact_id = NULL"
            " WHERE artifact_id IN (SELECT artifact_id FROM temp.pruned)")
        connection.execute(
            "DELETE FROM operational_artifacts WHERE artifact_id IN (SELECT artifact_id FROM temp.pruned)")
    connection.execute("PRAGMA incremental_vacuum").fetchall()  # hand freed pages back to the OS


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
        raise RecordError(f"operational artifact not found: {artifact_id} (if it was an observe"
                          f" result, it may have been {PRUNED_NOTE})")
    document = _row_to_dict(row)
    body = document.pop("body_json")
    if isinstance(body, bytes):  # current rows are compressed; older rows are JSON text
        body = zlib.decompress(body).decode("utf-8")
    document["body"] = json.loads(body)
    return document


def _normalize_receipt(receipt: dict[str, Any]) -> dict:
    if receipt.get("result_ok") is not None:
        receipt["result_ok"] = bool(receipt["result_ok"])
    if receipt.get("artifact_id") is None and receipt.get("status") != "started":
        receipt["artifact"] = PRUNED_NOTE  # every completed receipt had one
    return receipt

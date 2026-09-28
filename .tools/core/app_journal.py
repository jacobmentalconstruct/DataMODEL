from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import runtime_records, storage
from .instance import InstanceContext


class JournalError(RuntimeError):
    pass


_ENTRY_TYPES = {"entry", "decision", "backlog", "status"}
_STATUSES = {"open", "closed", "decided", "parked", "blocked"}
_TARGET_PREFIXES = {
    "operation": "operation:",
    "artifact": "artifact:",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _handle(value: int) -> str:
    return f"journal:{value}"


def _parse_handle(entry_id: str) -> int:
    if not entry_id.startswith("journal:"):
        raise JournalError("journal entry id must start with journal:")
    try:
        return int(entry_id.split(":", 1)[1])
    except ValueError as exc:
        raise JournalError("journal entry id has invalid numeric suffix") from exc


def _row_to_entry(row: sqlite3.Row) -> dict:
    return {
        "entry_id": _handle(row["entry_id"]),
        "created_at": row["created_at"],
        "entry_type": row["entry_type"],
        "status": row["status"],
        "title": row["title"],
        "body": row["body"],
    }


def add_entry(
    context: InstanceContext,
    *,
    entry_type: str,
    status: str,
    title: str,
    body: str,
) -> dict:
    entry_type = entry_type.strip().lower()
    status = status.strip().lower()
    title = title.strip()
    if entry_type not in _ENTRY_TYPES:
        raise JournalError(f"unsupported journal entry type: {entry_type}")
    if status not in _STATUSES:
        raise JournalError(f"unsupported journal status: {status}")
    if not title:
        raise JournalError("journal title is required")
    connection = storage.connect(context)
    try:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO app_journal_entries (created_at, entry_type, status, title, body)
                VALUES (?, ?, ?, ?, ?)
                """,
                (_now(), entry_type, status, title, body),
            )
            entry_pk = int(cursor.lastrowid)
        return read_entry(context, _handle(entry_pk))["entry"]
    finally:
        connection.close()


def list_entries(context: InstanceContext, limit: int = 50) -> list[dict]:
    connection = storage.connect(context)
    try:
        rows = connection.execute(
            """
            SELECT entry_id, created_at, entry_type, status, title, body
            FROM app_journal_entries
            ORDER BY entry_id
            LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    finally:
        connection.close()
    return [_row_to_entry(row) for row in rows]


def read_entry(context: InstanceContext, entry_id: str) -> dict:
    entry_pk = _parse_handle(entry_id)
    connection = storage.connect(context)
    try:
        row = connection.execute(
            """
            SELECT entry_id, created_at, entry_type, status, title, body
            FROM app_journal_entries
            WHERE entry_id = ?
            """,
            (entry_pk,),
        ).fetchone()
        if row is None:
            raise JournalError(f"journal entry not found: {entry_id}")
        links = connection.execute(
            """
            SELECT link_id, created_at, target_type, target_id
            FROM app_journal_links
            WHERE entry_id = ?
            ORDER BY link_id
            """,
            (entry_pk,),
        ).fetchall()
    finally:
        connection.close()
    return {
        "entry": _row_to_entry(row),
        "links": [
            {
                "link_id": f"journal-link:{link['link_id']}",
                "created_at": link["created_at"],
                "target_type": link["target_type"],
                "target_id": link["target_id"],
            }
            for link in links
        ],
    }


def link_entry(context: InstanceContext, entry_id: str, target_id: str) -> dict:
    entry_pk = _parse_handle(entry_id)
    target_type = _target_type(target_id)
    _verify_target(context, target_type, target_id)
    connection = storage.connect(context)
    try:
        with connection:
            if (
                connection.execute(
                    "SELECT 1 FROM app_journal_entries WHERE entry_id = ?",
                    (entry_pk,),
                ).fetchone()
                is None
            ):
                raise JournalError(f"journal entry not found: {entry_id}")
            cursor = connection.execute(
                """
                INSERT INTO app_journal_links (created_at, entry_id, target_type, target_id)
                VALUES (?, ?, ?, ?)
                """,
                (_now(), entry_pk, target_type, target_id),
            )
            link_id = int(cursor.lastrowid)
    finally:
        connection.close()
    return {
        "link_id": f"journal-link:{link_id}",
        "entry_id": entry_id,
        "target_type": target_type,
        "target_id": target_id,
    }


def _target_type(target_id: str) -> str:
    for target_type, prefix in _TARGET_PREFIXES.items():
        if target_id.startswith(prefix):
            return target_type
    raise JournalError("journal links may target only operation: or artifact: identifiers")


def _verify_target(context: InstanceContext, target_type: str, target_id: str) -> None:
    try:
        if target_type == "operation":
            runtime_records.read_receipt(context, target_id)
        elif target_type == "artifact":
            runtime_records.read_artifact(context, target_id)
    except runtime_records.RecordError as exc:
        raise JournalError(str(exc)) from exc

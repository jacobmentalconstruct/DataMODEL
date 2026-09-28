from __future__ import annotations

import sqlite3
from pathlib import Path

from .constants import DATABASE_SCHEMA_VERSION
from .instance import InstanceContext


class StorageError(RuntimeError):
    pass


def database_path(context: InstanceContext) -> Path:
    return context.state_root / "workbench.sqlite3"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


def _migrate(
    connection: sqlite3.Connection,
    *,
    target_version: int = DATABASE_SCHEMA_VERSION,
) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if target_version > DATABASE_SCHEMA_VERSION:
        raise StorageError(
            f"target schema {target_version} is newer than this runtime supports "
            f"({DATABASE_SCHEMA_VERSION})"
        )
    if version > target_version:
        raise StorageError(
            f"database schema {version} is newer than this runtime supports "
            f"({target_version})"
        )
    if version == 0:
        with connection:
            connection.execute(
                """
                CREATE TABLE instances (
                    instance_uuid TEXT PRIMARY KEY,
                    instance_schema_version INTEGER NOT NULL,
                    product_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    target_relation TEXT NOT NULL CHECK (target_relation = '..')
                )
                """
            )
            connection.execute("PRAGMA user_version = 1")
        version = 1
    if target_version < 2:
        return
    if version < 2:
        with connection:
            connection.execute(
                """
                CREATE TABLE operational_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    body_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE operation_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    instance_uuid TEXT NOT NULL REFERENCES instances(instance_uuid),
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    client TEXT NOT NULL,
                    tool_id TEXT NOT NULL,
                    authority TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    result_ok INTEGER,
                    exit_code INTEGER,
                    duration_ms INTEGER,
                    manifest_digest TEXT,
                    artifact_id TEXT REFERENCES operational_artifacts(artifact_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE app_journal_entries (
                    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE app_journal_links (
                    link_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    entry_id INTEGER NOT NULL REFERENCES app_journal_entries(entry_id)
                        ON DELETE CASCADE,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL
                )
                """
            )
            connection.execute("PRAGMA user_version = 2")
        version = 2
    if target_version < 3:
        return
    if version < 3:
        with connection:
            connection.execute(
                """
                CREATE TABLE resources (
                    resource_id TEXT PRIMARY KEY,
                    handle TEXT NOT NULL UNIQUE,
                    path TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    latest_version_id TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE epistemic_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    digest TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    body_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE resource_versions (
                    version_id TEXT PRIMARY KEY,
                    resource_id TEXT NOT NULL REFERENCES resources(resource_id),
                    observed_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content_hash TEXT,
                    size_bytes INTEGER,
                    mtime_ns INTEGER,
                    evidence_id TEXT NOT NULL REFERENCES epistemic_evidence(evidence_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE observations (
                    observation_id TEXT PRIMARY KEY,
                    producer TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    subject_handle TEXT NOT NULL,
                    observation_type TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    evidence_id TEXT NOT NULL REFERENCES epistemic_evidence(evidence_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE claims (
                    claim_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    claim_type TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    derivation_method TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    data_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE relations (
                    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_type TEXT NOT NULL,
                    object_id TEXT NOT NULL
                )
                """
            )
            connection.execute("PRAGMA user_version = 3")
        version = 3
    if target_version < 4:
        return
    if version < 4:
        with connection:
            connection.execute(
                """
                CREATE TABLE awareness_revisions (
                    awareness_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    basis_status TEXT NOT NULL,
                    basis_signature TEXT,
                    target_signature TEXT,
                    summary_json TEXT NOT NULL,
                    limitations_json TEXT NOT NULL,
                    unknowns_json TEXT NOT NULL,
                    source_handles_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE awareness_items (
                    item_id TEXT PRIMARY KEY,
                    awareness_id TEXT NOT NULL REFERENCES awareness_revisions(awareness_id),
                    item_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    source_handles_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL
                )
                """
            )
            connection.execute("PRAGMA user_version = 4")
        version = 4
    if target_version < 5:
        return
    if version < 5:
        with connection:
            connection.execute(
                """
                CREATE TABLE mutation_previews (
                    preview_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    instance_uuid TEXT NOT NULL REFERENCES instances(instance_uuid),
                    operation TEXT NOT NULL,
                    path TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    before_exists INTEGER NOT NULL,
                    before_digest TEXT,
                    after_digest TEXT NOT NULL,
                    overwrite INTEGER NOT NULL,
                    expected_changed_paths_json TEXT NOT NULL,
                    awareness_id TEXT NOT NULL,
                    basis_signature TEXT,
                    target_signature TEXT NOT NULL,
                    preview_digest TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE mutation_approvals (
                    approval_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    instance_uuid TEXT NOT NULL REFERENCES instances(instance_uuid),
                    preview_id TEXT NOT NULL REFERENCES mutation_previews(preview_id),
                    preview_digest TEXT NOT NULL,
                    basis_signature TEXT,
                    target_signature TEXT NOT NULL,
                    status TEXT NOT NULL,
                    journal_entry_id TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE mutation_verifications (
                    verification_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    method TEXT NOT NULL,
                    detail_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE mutation_records (
                    mutation_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    instance_uuid TEXT NOT NULL REFERENCES instances(instance_uuid),
                    preview_id TEXT NOT NULL REFERENCES mutation_previews(preview_id),
                    approval_id TEXT,
                    status TEXT NOT NULL,
                    refusal_code TEXT,
                    receipt_id TEXT,
                    artifact_id TEXT,
                    measurement_json TEXT NOT NULL,
                    verification_id TEXT,
                    pre_awareness_id TEXT,
                    post_awareness_id TEXT,
                    substrate_refresh_json TEXT NOT NULL,
                    detail_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE mutation_links (
                    link_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL
                )
                """
            )
            connection.execute("PRAGMA user_version = 5")
        version = 5
    if target_version < 6:
        return
    if version < 6:
        with connection:
            connection.execute("ALTER TABLE mutation_records RENAME TO mutation_records_v5")
            connection.execute(
                """
                CREATE TABLE mutation_records (
                    mutation_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    instance_uuid TEXT NOT NULL REFERENCES instances(instance_uuid),
                    preview_id TEXT REFERENCES mutation_previews(preview_id),
                    approval_id TEXT,
                    status TEXT NOT NULL,
                    refusal_code TEXT,
                    receipt_id TEXT,
                    artifact_id TEXT,
                    measurement_json TEXT NOT NULL,
                    verification_id TEXT,
                    pre_awareness_id TEXT,
                    post_awareness_id TEXT,
                    substrate_refresh_json TEXT NOT NULL,
                    detail_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO mutation_records
                    (mutation_id, created_at, instance_uuid, preview_id, approval_id,
                     status, refusal_code, receipt_id, artifact_id, measurement_json,
                     verification_id, pre_awareness_id, post_awareness_id,
                     substrate_refresh_json, detail_json)
                SELECT mutation_id, created_at, instance_uuid, preview_id, approval_id,
                       status, refusal_code, receipt_id, artifact_id, measurement_json,
                       verification_id, pre_awareness_id, post_awareness_id,
                       substrate_refresh_json, detail_json
                FROM mutation_records_v5
                """
            )
            connection.execute("DROP TABLE mutation_records_v5")
            connection.execute("PRAGMA user_version = 6")
        version = 6
    if target_version < 7:
        return
    if version < 7:
        # Lookups the substrate performs per snapshot; without these every one is a scan.
        with connection:
            for statement in (
                "CREATE INDEX IF NOT EXISTS observations_subject"
                " ON observations(producer, subject_handle, observation_type)",
                "CREATE INDEX IF NOT EXISTS observations_type ON observations(observation_type)",
                "CREATE INDEX IF NOT EXISTS observations_evidence ON observations(evidence_id)",
                "CREATE INDEX IF NOT EXISTS relations_subject ON relations(subject_id)",
                "CREATE INDEX IF NOT EXISTS relations_object ON relations(object_id)",
                "CREATE INDEX IF NOT EXISTS resource_versions_resource ON resource_versions(resource_id)",
                "CREATE INDEX IF NOT EXISTS epistemic_evidence_kind ON epistemic_evidence(kind)",
            ):
                connection.execute(statement)
            connection.execute("PRAGMA user_version = 7")
        version = 7
    if target_version < 8:
        return
    if version < 8:
        # Observation and version relations are now derived from row columns on read, so
        # stored copies are dropped; bodies are compressed from here on (old rows stay
        # readable). Incremental auto-vacuum lets pruning hand space back to the OS.
        with connection:
            connection.execute("DELETE FROM relations WHERE subject_type IN ('observation', 'version')")
            connection.execute("PRAGMA user_version = 8")
        connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
        connection.execute("VACUUM")  # applies auto_vacuum to an existing file (one time)


def _instances_table_exists(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'instances'"
        ).fetchone()
        is not None
    )


def _verify_existing_identity(connection: sqlite3.Connection, context: InstanceContext) -> None:
    if not _instances_table_exists(connection):
        return
    rows = connection.execute("SELECT instance_uuid FROM instances").fetchall()
    if rows and (len(rows) != 1 or rows[0]["instance_uuid"] != context.instance_uuid):
        raise StorageError(
            "SQLite instance identity does not agree with instance.json; refusing re-entry"
        )


def bootstrap(context: InstanceContext) -> Path:
    path = database_path(context)
    connection = _connect(path)
    try:
        _verify_existing_identity(connection, context)
        _migrate(connection)
        rows = connection.execute("SELECT * FROM instances").fetchall()
        if not rows:
            with connection:
                connection.execute(
                    "INSERT INTO instances VALUES (?, ?, ?, ?, ?)",
                    (
                        context.instance_uuid,
                        context.schema_version,
                        context.product_version,
                        context.created_at,
                        context.target_relation,
                    ),
                )
        else:
            _verify_existing_identity(connection, context)
    finally:
        connection.close()
    return path


def connect(context: InstanceContext) -> sqlite3.Connection:
    path = bootstrap(context)
    connection = _connect(path)
    try:
        _verify_existing_identity(connection, context)
    except Exception:
        connection.close()
        raise
    return connection

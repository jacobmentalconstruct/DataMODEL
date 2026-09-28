from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import storage
from .fswalk import IgnoreRules, PathFilter, parse_rule_file
from .instance import InstanceContext


class SubstrateError(RuntimeError):
    pass


TABLES = (
    "resources",
    "resource_versions",
    "observations",
    "epistemic_evidence",
    "claims",
    "relations",
)

_TEXT_SUFFIXES = {
    ".cfg",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".rst",
    ".toml",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

_SOFTWARE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css"}
_SOFTWARE_FILES = {
    "pyproject.toml",
    "package.json",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "cargo.toml",
    "go.mod",
    "makefile",
}
_RECORD_SUFFIXES = {".csv", ".tsv", ".sqlite", ".db", ".xlsx"}
_DOCUMENT_SUFFIXES = {".md", ".rst", ".pdf", ".doc", ".docx", ".rtf", ".txt"}
_CONFIG_DATA_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg"}
_ANCILLARY_DOCUMENT_SUFFIXES = {".md", ".rst", ".txt"}
_ANCILLARY_DOCUMENT_STEMS = (
    "readme",
    "license",
    "licence",
    "changelog",
    "contributing",
    "notice",
    "authors",
    "copying",
)
_BINARY_MEDIA_SUFFIXES = {
    ".bin",
    ".dat",
    ".gif",
    ".jpg",
    ".jpeg",
    ".mp3",
    ".mp4",
    ".png",
    ".webp",
    ".zip",
}
_UNPARSED_DOCUMENT_SUFFIXES = {".pdf", ".doc", ".docx", ".xlsx"}
_VENDOR_PARTS = {"node_modules", "vendor", ".venv", "venv"}
_GENERATED_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "__pycache__",
    "build",
    "dist",
}
# Ordinary folder names that only mean vendor/generated material on a software target:
# they are treated as untraversed only when a software marker exists at or above them.
_SOFTWARE_CONDITIONAL_PARTS = {"vendor", "build", "dist"}
_LARGE_FILE_BYTES = 1_000_000
# Snapshots whose derived rows (inventory observations, claims) are retained; per-resource
# observations and versions are kept whenever they record a change, so history of change
# is never lost, only repeated re-statements of unchanged facts.
_RETAINED_SNAPSHOTS = 3
_SQL_CHUNK = 500
SNAPSHOT_IGNORE = "snapshot-ignore"  # .tools/snapshot-ignore, gitignore syntax
_METADATA_ONLY_FRESHNESS = (
    "content changes to this material are detected only through size and modification time"
)
_UNTRAVERSED_SUBTREE = (
    "its contents were not traversed and remain unobserved; changes inside it are detected"
    " only through the directory's own modification time"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_bytes(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _evidence_id(digest: str) -> str:
    return f"evidence:{digest}"


def _uuid_handle(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4().hex}"


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


# Observation data that is exactly derivable from its evidence body is stored as this
# marker instead of a second copy (see _decode_observations).
_FROM_EVIDENCE = "@evidence"


def _unpack(value: object) -> Any:
    """Read a stored body: compressed bytes (current) or JSON text (older rows)."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = zlib.decompress(bytes(value)).decode("utf-8")
    return json.loads(value)


def _decode(row: sqlite3.Row, field: str = "data_json") -> dict:
    document = _row_to_dict(row)
    document[field.replace("_json", "")] = _unpack(document.pop(field))
    return document


def _resource_handle(relative: str, kind: str) -> str:
    if relative == ".":
        return "path:."
    suffix = "/" if kind == "directory" and not relative.endswith("/") else ""
    return f"path:{relative}{suffix}"


def _resource_id(path: str) -> str:
    return f"path:{path}"


def _basis_id(signature: str) -> str:
    return f"basis:{signature[:32]}"


def _version_id(handle: str, evidence_id: str, mtime_ns: int | None) -> str:
    digest = hashlib.sha256(f"{handle}\0{evidence_id}\0{mtime_ns}".encode("utf-8")).hexdigest()
    return f"version:{digest[:32]}"


def _is_text_like(path: str) -> bool:
    return Path(path).suffix.lower() in _TEXT_SUFFIXES


def _untraversed_subtree_kind(name: str, *, software_context: bool = True) -> str | None:
    lowered = name.lower()
    if lowered in _SOFTWARE_CONDITIONAL_PARTS and not software_context:
        return None
    if lowered in _VENDOR_PARTS:
        return "vendor_dependency"
    if lowered in _GENERATED_PARTS:
        return "generated"
    return None


def _is_software_file(name: str) -> bool:
    lowered = name.lower()
    return Path(lowered).suffix in _SOFTWARE_SUFFIXES or lowered in _SOFTWARE_FILES


_TEXT_DOMINANCE_RATIO = 2


def _text_documents_dominate(text_documents: list[dict], software: list[dict]) -> bool:
    """Text documents stop being software ancillary once they outnumber software signals
    by more than the dominance ratio; a project's documentation may exceed its source
    count, but a notes collection with a few helper scripts exceeds it many times over."""
    return len(text_documents) > _TEXT_DOMINANCE_RATIO * len(software)


def _is_named_ancillary_document(name: str) -> bool:
    return Path(name).stem.lower().startswith(_ANCILLARY_DOCUMENT_STEMS)


def _is_ancillary_document(name: str, suffix: str) -> bool:
    if suffix in _ANCILLARY_DOCUMENT_SUFFIXES:
        return True
    return suffix == "" and _is_named_ancillary_document(name)


def _domain_signal(record: dict) -> dict:
    path = record["path"]
    suffix = Path(path).suffix.lower()
    name = Path(path).name.lower()
    size = int(record.get("size_bytes") or 0)
    subtree_kind = (
        "bulk" if record.get("bulk")
        else _untraversed_subtree_kind(name, software_context=record.get("software_context", True))
        if record["kind"] == "directory"
        else None
    )
    categories: list[str] = []
    signals: list[str] = []
    limitations: list[str] = []
    weak_material = False
    ancillary = False
    text_document = False

    if subtree_kind == "vendor_dependency":
        categories.append("vendor_dependency")
        signals.append("vendor/dependency-like path")
        limitations.append(
            "vendor/dependency-like material is represented as metadata only; "
            + _UNTRAVERSED_SUBTREE
        )
        weak_material = True
    elif subtree_kind == "bulk":
        categories.append("bulk")
        signals.append("listed in .tools/snapshot-ignore")
        limitations.append(
            "listed in .tools/snapshot-ignore, so tracked as a folder only; " + _UNTRAVERSED_SUBTREE
        )
        weak_material = True
    elif subtree_kind == "generated":
        categories.append("generated")
        signals.append("version-control, build, or cache subtree")
        limitations.append(
            "generated/version-control material is represented as metadata only; "
            + _UNTRAVERSED_SUBTREE
        )
        weak_material = True
    if record["kind"] == "file":
        if suffix in _SOFTWARE_SUFFIXES or name in _SOFTWARE_FILES:
            categories.append("software")
            signals.append("software file or project marker")
        elif suffix in _CONFIG_DATA_SUFFIXES:
            categories.append("config_data")
            signals.append("configuration or structured-data file")
            ancillary = True
        if suffix in _RECORD_SUFFIXES:
            categories.append("records")
            signals.append("records/data file marker")
        if suffix in _DOCUMENT_SUFFIXES or _is_ancillary_document(name, suffix):
            categories.append("documents")
            signals.append("document file marker")
            ancillary = _is_ancillary_document(name, suffix)
            text_document = ancillary and not _is_named_ancillary_document(name)
        if suffix in _UNPARSED_DOCUMENT_SUFFIXES:
            limitations.append("unparsed document body; content understanding is unknown")
            weak_material = True
        if suffix in _BINARY_MEDIA_SUFFIXES:
            limitations.append(
                "binary/media-like material is represented as metadata only; "
                + _METADATA_ONLY_FRESHNESS
            )
            weak_material = True
        if size >= _LARGE_FILE_BYTES:
            limitations.append(
                "large file is represented without content-heavy inspection; "
                + _METADATA_ONLY_FRESHNESS
            )
            weak_material = True

    return {
        "categories": sorted(set(categories)),
        "signals": sorted(set(signals)),
        "limitations": sorted(set(limitations)),
        "weak_material": weak_material,
        "ancillary": ancillary,
        "text_document": text_document,
        "content_basis": "metadata_only" if weak_material else "metadata_and_hash",
    }


def _observation_type(record: dict) -> str:
    if record["kind"] != "file":
        return "resource_seen"
    if record["domain"]["content_basis"] == "metadata_only":
        return "file_metadata"
    return "file_hash"


def _insert_evidence(
    connection: sqlite3.Connection,
    *,
    kind: str,
    body: dict,
    created_at: str,
) -> str:
    payload = _json_bytes(body)
    digest = hashlib.sha256(payload).hexdigest()
    evidence_id = _evidence_id(digest)
    connection.execute(
        """
        INSERT OR IGNORE INTO epistemic_evidence
            (evidence_id, digest, created_at, kind, media_type, body_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (evidence_id, digest, created_at, kind, "application/json", zlib.compress(payload, 6)),
    )
    return evidence_id


def _insert_relation(
    connection: sqlite3.Connection,
    *,
    subject_type: str,
    subject_id: str,
    predicate: str,
    object_type: str,
    object_id: str,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO relations
            (created_at, subject_type, subject_id, predicate, object_type, object_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (created_at, subject_type, subject_id, predicate, object_type, object_id),
    )


def _insert_claim(
    connection: sqlite3.Connection,
    *,
    claim_type: str,
    statement: str,
    derivation_method: str,
    confidence: float,
    data: dict,
    observation_ids: list[str],
    created_at: str,
) -> str:
    """A claim derives from a set of observations. The set is stored once, as
    content-addressed evidence, with one relation to it; per-member "derived_from" edges
    and the members' evidence are expanded on read (_node_relations)."""
    claim_id = _uuid_handle("claim")
    connection.execute(
        """
        INSERT INTO claims
            (claim_id, created_at, claim_type, statement, derivation_method,
             confidence, data_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            claim_id,
            created_at,
            claim_type,
            statement,
            derivation_method,
            confidence,
            json.dumps(data, sort_keys=True),
        ),
    )
    members = _insert_evidence(
        connection,
        kind="observation_set",
        body={"observations": sorted(set(observation_ids))},
        created_at=created_at,
    )
    _insert_relation(
        connection,
        subject_type="claim",
        subject_id=claim_id,
        predicate="derived_from_set",
        object_type="evidence",
        object_id=members,
        created_at=created_at,
    )
    return claim_id


def _insert_domain_claims(
    connection: sqlite3.Connection,
    *,
    observations: list[dict],
    created_at: str,
) -> int:
    if not observations:
        return 0
    by_category: dict[str, list[dict]] = {}
    weak = []
    for observation in observations:
        for category in observation["categories"]:
            by_category.setdefault(category, []).append(observation)
        if observation["weak_material"]:
            weak.append(observation)

    count = 0
    software = by_category.get("software", [])
    documents = by_category.get("documents", [])
    records = by_category.get("records", [])
    config_data = by_category.get("config_data", [])
    profile = _profile_decision(
        software=software,
        documents=documents,
        records=records,
        config_data=config_data,
    )
    if software:
        limitations = [
            "software profile is based on deterministic file and marker signals only",
            "language symbols and imports have not been analyzed by T7",
        ]
        if profile["subordinate_count"]:
            limitations.append(
                "records/document material beside the software signals is subordinate by count"
                " and is not treated as a second domain profile"
            )
        _insert_claim(
            connection,
            claim_type="target_profile_software",
            statement="target has deterministic software-project signals",
            derivation_method="deterministic.domain_signals",
            confidence=0.9 if len(software) >= 2 else 0.75,
            data={
                "domain_profile": "software",
                "software_signal_count": len(software),
                "ancillary_document_count": profile["ancillary_document_count"],
                "ancillary_config_count": len(config_data),
                "subordinate_records_document_count": profile["subordinate_count"],
                "supporting_handles": _handles(software),
                "limitations": limitations,
            },
            observation_ids=_observation_ids(software),
            created_at=created_at,
        )
        count += 1
    if profile["records_documents"]:
        supporting = profile["records_documents"]
        _insert_claim(
            connection,
            claim_type="target_profile_records_documents",
            statement="target has deterministic records/document collection signals",
            derivation_method="deterministic.domain_signals",
            confidence=0.85 if documents and records else 0.75,
            data={
                "domain_profile": "records_documents",
                "document_signal_count": len(documents),
                "record_signal_count": len(records),
                "config_data_signal_count": len(config_data) if not software else 0,
                "decision": profile["decision"],
                "supporting_handles": _handles(supporting),
                "limitations": [
                    "records/document profile is based on deterministic file signals only",
                    "document bodies are not parsed unless a deterministic parser produced evidence",
                ],
            },
            observation_ids=_observation_ids(supporting),
            created_at=created_at,
        )
        count += 1
    if weak:
        limitations = sorted({limit for item in weak for limit in item["limitations"]})
        _insert_claim(
            connection,
            claim_type="target_has_weak_material",
            statement="target contains weakly observed material represented with limited basis",
            derivation_method="deterministic.domain_signals",
            confidence=1.0,
            data={
                "content_basis": "metadata_only",
                "weak_material_count": len(weak),
                "supporting_handles": _handles(weak),
                "limitations": limitations,
            },
            observation_ids=_observation_ids(weak),
            created_at=created_at,
        )
        count += 1
    return count


def _profile_decision(
    *,
    software: list[dict],
    documents: list[dict],
    records: list[dict],
    config_data: list[dict],
) -> dict:
    """Decide which profile claims the deterministic signals support.

    Without software signals, any records, documents, or configuration/data files support
    a records/documents profile. Beside software signals, README-style named files and
    configuration files are software ancillary; plain-text documents (`.md`, `.rst`,
    `.txt`) are ancillary only while they do not outnumber the software signals by more
    than the dominance ratio (2:1). The
    remaining records/documents support a second profile when they are substantive by
    count (at least two and at least one fifth of the software signals).
    """
    text_documents = [item for item in documents if item.get("text_document")]
    text_dominates = _text_documents_dominate(text_documents, software)
    ancillary_documents = [
        item
        for item in documents
        if item.get("ancillary") and not (text_dominates and item.get("text_document"))
    ]
    strong = [
        *records,
        *[item for item in documents if not item.get("ancillary")],
        *(text_documents if text_dominates else []),
    ]
    if not software:
        candidates = [*records, *documents, *config_data]
        decision = "records_documents_without_software" if candidates else "no_signals"
        return {
            "records_documents": candidates,
            "decision": decision,
            "ancillary_document_count": 0,
            "subordinate_count": 0,
        }
    substantive = len(strong) >= 2 and len(strong) * 5 >= len(software)
    if substantive:
        decision = "mixed_text_documents_dominate" if text_dominates else "mixed_by_count"
    else:
        decision = "software_with_ancillary_material"
    return {
        "records_documents": strong if substantive else [],
        "decision": decision,
        "ancillary_document_count": len(ancillary_documents),
        "subordinate_count": 0 if substantive else len(strong),
    }


def _handles(observations: list[dict]) -> list[str]:
    return sorted({item["resource_handle"] for item in observations})


def _observation_ids(observations: list[dict]) -> list[str]:
    return [item["observation_id"] for item in observations]


def _snapshot_rules(context: InstanceContext) -> IgnoreRules | None:
    rule_file = context.instance_root / SNAPSHOT_IGNORE
    return parse_rule_file(rule_file) if rule_file.is_file() else None


def _known_versions(connection: sqlite3.Connection) -> dict[str, dict]:
    """Latest recorded version of every resource, by path: lets a walk reuse a content
    hash when size and mtime are unchanged (the same test git uses)."""
    rows = connection.execute(
        """
        SELECT r.path, r.handle, r.kind, v.content_hash, v.size_bytes, v.mtime_ns
        FROM resources AS r
        LEFT JOIN resource_versions AS v ON v.version_id = r.latest_version_id
        """
    ).fetchall()
    return {row["path"]: _row_to_dict(row) for row in rows}


def _resource_records(
    context: InstanceContext,
    known: dict[str, dict] | None = None,
    stats: dict | None = None,
) -> list[dict]:
    records: list[dict] = []
    excluded = context.instance_root.resolve()
    software_context: dict[Path, bool] = {}
    access_errors: dict[str, str] = {}
    rules = _snapshot_rules(context)
    skipped_files = 0

    def record_access_error(error: OSError) -> None:
        filename = getattr(error, "filename", None)
        if not filename:
            return
        try:
            relative = Path(filename).relative_to(context.target_root).as_posix()
        except ValueError:
            return
        access_errors[relative] = f"{type(error).__name__}: {error}"

    root = str(context.target_root)
    for current, directory_names, file_names in os.walk(root, onerror=record_access_error):
        here = Path(current)
        prefix = os.path.relpath(current, root).replace(os.sep, "/")
        prefix = "" if prefix == "." else prefix + "/"
        in_software = software_context.get(here.parent, False) or any(
            _is_software_file(name) for name in file_names
        )
        software_context[here] = in_software
        directory_names[:] = [
            name
            for name in sorted(directory_names)
            if not _inside_or_equal((here / name).resolve(strict=False), excluded)
        ]
        traversed: list[str] = []
        for name in directory_names:
            path = here / name
            kind = "symlink" if path.is_symlink() else "directory"
            relative = prefix + name
            bulk = kind == "directory" and rules is not None and rules.ignored(relative, True)
            records.append(_describe_resource(context, path, kind, software_context=in_software,
                                              bulk=bulk, relative=relative))
            if kind == "directory" and (bulk or _untraversed_subtree_kind(name, software_context=in_software)):
                continue
            traversed.append(name)
        directory_names[:] = traversed
        for name in sorted(file_names):
            relative = prefix + name
            if rules is not None and rules.ignored(relative, False):
                skipped_files += 1
                continue
            path = here / name
            kind = "symlink" if os.path.islink(path) else "file"
            records.append(_describe_resource(context, path, kind, known=known, relative=relative))
    for record in records:
        if record["path"] in access_errors:
            _mark_access_limited(record, access_errors[record["path"]])
    if stats is not None:
        stats["snapshot_ignored_files"] = skipped_files
    return records


def _untraversed_subtrees(records: list[dict]) -> list[str]:
    return [
        record["handle"]
        for record in records
        if record["kind"] == "directory" and record["domain"]["weak_material"]
    ]


def _inventory_limitations(records: list[dict]) -> list[str]:
    untraversed = _untraversed_subtrees(records)
    if not untraversed:
        return []
    return [
        f"{len(untraversed)} vendor, generated, or version-control subtree(s) were recorded as"
        " metadata only and not traversed: " + ", ".join(untraversed)
    ]


def _resource_signature(records: list[dict]) -> str:
    payload = [
        {
            "handle": record["handle"],
            "kind": record["kind"],
            "path": record["path"],
            "size_bytes": record.get("size_bytes"),
            "mtime_ns": record.get("mtime_ns"),
            "content_hash": record.get("content_hash"),
        }
        for record in sorted(records, key=lambda item: item["handle"])
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _inside_or_equal(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _describe_resource(
    context: InstanceContext,
    path: Path,
    kind: str,
    *,
    software_context: bool = True,
    known: dict[str, dict] | None = None,
    bulk: bool = False,
    relative: str | None = None,
) -> dict:
    if relative is None:
        relative = path.relative_to(context.target_root).as_posix()
    stat = os.lstat(path)
    record = {
        "handle": _resource_handle(relative, kind),
        "path": relative,
        "kind": kind,
        "size_bytes": stat.st_size if kind == "file" else None,
        "mtime_ns": stat.st_mtime_ns,
        "content_hash": None,
        "text_like": kind == "file" and _is_text_like(relative),
    }
    record["domain"] = _domain_signal({**record, "software_context": software_context, "bulk": bulk})
    if kind == "file" and record["domain"]["content_basis"] != "metadata_only":
        prior = (known or {}).get(relative)
        if (prior and prior.get("content_hash") and prior.get("kind") == "file"
                and prior.get("size_bytes") == stat.st_size and prior.get("mtime_ns") == stat.st_mtime_ns):
            record["content_hash"] = prior["content_hash"]  # unchanged size+mtime: reuse
            return record
        try:
            record["content_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            _mark_access_limited(record, f"{type(exc).__name__}: {exc}")
    return record


def _mark_access_limited(record: dict, detail: str) -> None:
    domain = record["domain"]
    domain["categories"] = sorted({*domain["categories"], "access_limited"})
    domain["signals"] = sorted({*domain["signals"], "access error during deterministic inspection"})
    domain["limitations"] = sorted(
        {
            *domain["limitations"],
            "resource could not be fully inspected because access failed; unobserved contents remain unknown",
        }
    )
    domain["weak_material"] = True
    domain["content_basis"] = "metadata_only"
    domain["access_error"] = detail
    record["content_hash"] = None


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


def target_signature(context: InstanceContext) -> str:
    connection = storage.connect(context)
    try:
        known = _known_versions(connection)
    finally:
        connection.close()
    return _resource_signature(_resource_records(context, known))


def _chunked(values: list, size: int = _SQL_CHUNK):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _signed_size(delta: int) -> str:
    return f"+{delta}B" if delta >= 0 else f"{delta}B"


def changes(context: InstanceContext, limit: int = 200, *, respect_gitignore: bool = False) -> dict:
    """Diff the live target against the last refresh snapshot, without recording anything.

    A file counts as modified when its content hash differs or, for resources the refresh
    records as metadata only, when size or mtime differ. Directory mtimes are ignored: they
    change whenever a child does and would only repeat the file-level findings.

    With respect_gitignore, paths excluded by .gitignore (and vendor directories) are
    dropped from both sides, so they can appear neither as changes nor as removals.
    """
    connection = storage.connect(context)
    try:
        inventory = connection.execute(
            """
            SELECT o.observation_id, o.observed_at, e.body_json
            FROM observations AS o
            JOIN epistemic_evidence AS e ON e.evidence_id = o.evidence_id
            WHERE o.observation_type = 'resource_inventory'
            ORDER BY o.rowid DESC
            LIMIT 1
            """
        ).fetchone()
        baseline: dict[str, dict] = {}
        known = _known_versions(connection)
        if inventory is not None:
            handles = set(_unpack(inventory["body_json"]).get("handles", []))
            baseline = {path: row for path, row in known.items() if row["handle"] in handles}
    finally:
        connection.close()

    current = {record["path"]: record for record in _resource_records(context, known)}
    scope_note = ""
    if respect_gitignore:
        path_filter = PathFilter(context.target_root)

        def visible(records: dict[str, dict]) -> dict[str, dict]:
            return {
                path: record for path, record in records.items()
                if not path_filter.ignored(path, record["kind"] == "directory")
            }

        current, baseline = visible(current), visible(baseline)
        scope_note = " (respecting .gitignore)"
    if inventory is None:
        files = sum(1 for record in current.values() if record["kind"] == "file")
        return {
            "ok": True,
            "baseline": None,
            "added": sorted(current),
            "removed": [],
            "modified": [],
            "uncertain": [],
            "untracked": [],
            "text": f"no snapshot yet ({files} files present{scope_note}); mark=true records a baseline",
        }

    added = sorted(set(current) - set(baseline))
    removed = sorted(set(baseline) - set(current))
    modified: list[dict] = []
    # Subtrees the refresh records as metadata only (vendor, generated, version control,
    # unreadable): their contents are unobserved, so silence about them is not "no change".
    untracked = sorted(
        path for path, record in current.items()
        if record["kind"] == "directory" and record["domain"].get("weak_material")
    )
    uncertain: list[str] = []
    for path in sorted(set(current) & set(baseline)):
        now, before = current[path], baseline[path]
        if now["kind"] != "file" or before["kind"] != "file":
            if now["kind"] != before["kind"]:
                modified.append({"path": path, "change": f"{before['kind']} -> {now['kind']}"})
            elif path in untracked and now.get("mtime_ns") != before.get("mtime_ns"):
                uncertain.append(path)
            continue
        if now.get("content_hash") and before.get("content_hash"):
            differs = now["content_hash"] != before["content_hash"]
        else:
            differs = (now.get("size_bytes"), now.get("mtime_ns")) != (
                before.get("size_bytes"), before.get("mtime_ns"))
        if differs:
            delta = (now.get("size_bytes") or 0) - (before.get("size_bytes") or 0)
            modified.append({"path": path, "change": _signed_size(delta)})

    def label(path: str, records: dict) -> str:
        return path + ("/" if records[path]["kind"] == "directory" else "")

    lines = [f"M {item['path']}  ({item['change']})" for item in modified]
    lines += [f"A {label(path, current)}" for path in added]
    lines += [f"D {label(path, baseline)}" for path in removed]
    total = len(lines)
    lines += [f"? {path}/  (folder changed; contents untracked)" for path in uncertain]
    header = f"changes since snapshot {inventory['observed_at']}{scope_note}: {total}"
    if uncertain:
        header += f", {len(uncertain)} uncertain"
    if len(lines) > limit:
        lines = lines[:limit] + [f"# … {len(lines) - limit} more; raise limit"]
    if untracked:
        lines.append(
            "# contents untracked: " + ", ".join(path + "/" for path in untracked)
            + " (ls/grep all=true can look)"
        )
    text = header if not lines else header + "\n" + "\n".join(lines)
    return {
        "ok": True,
        "baseline": {"observation_id": inventory["observation_id"], "observed_at": inventory["observed_at"]},
        "added": added,
        "removed": removed,
        "modified": modified,
        "uncertain": uncertain,
        "untracked": untracked,
        "text": text,
    }


def current_awareness_basis(context: InstanceContext) -> dict:
    connection = storage.connect(context)
    try:
        inventory = connection.execute(
            """
            SELECT rowid AS row_number, observation_id, producer, observed_at, subject_handle,
                   observation_type, data_json, evidence_id
            FROM observations
            WHERE producer = 'substrate.resource_inventory'
              AND subject_handle = 'path:.'
              AND observation_type = 'resource_inventory'
            ORDER BY rowid DESC
            LIMIT 1
            """
        ).fetchone()
        if inventory is None:
            return {
                "status": "missing",
                "basis_id": None,
                "basis_signature": None,
                "observed_at": None,
                "target_signature": None,
                "resource_handles": [],
                "resources": [],
                "claims": [],
                "observations": [],
                "evidence_handles": [],
                "provenance_handles": [],
                "source_handles": [],
            }

        inventory_document = _decode(inventory)
        inventory_evidence = read_evidence(context, inventory_document["evidence_id"])
        resource_handles = list(inventory_evidence["body"].get("handles", []))
        recorded = inventory_evidence["body"].get("observations")
        if recorded is not None:  # snapshot lists its exact observation set (reused + new)
            current_observations = _observations_by_id(
                connection, [inventory_document["observation_id"], *recorded])
        else:  # snapshots written before observation reuse
            current_observations = _current_refresh_observations(
                connection,
                inventory_row=int(inventory["row_number"]),
                inventory_observation_id=inventory_document["observation_id"],
                resource_handles=resource_handles,
            )
        resources = _current_refresh_resources(connection, resource_handles)
        claims = _claims_for_snapshot(connection, inventory_document["observed_at"])
        relations = _relations_for_basis(
            connection,
            observations=current_observations,
            resources=resources,
            claim_ids=[item["claim_id"] for item in claims],
        )
        resource_records = [
            item["data"]
            for item in current_observations
            if item["observation_type"] in {"file_hash", "file_metadata", "resource_seen"}
        ]
        observed_target_signature = _resource_signature(resource_records)
        evidence_handles = sorted(
            {
                inventory_document["evidence_id"],
                *[item["evidence_id"] for item in current_observations],
                *[
                    relation["object_id"]
                    for relation in relations
                    if relation["object_type"] == "evidence"
                ],
            }
        )
        provenance_handles = [f"relation:{item['relation_id']}" for item in relations]
        source_handles = [
            *resource_handles,
            *[item["latest_version_id"] for item in resources if item.get("latest_version_id")],
            *[item["observation_id"] for item in current_observations],
            *evidence_handles,
            *[item["claim_id"] for item in claims],
            *provenance_handles,
        ]
        signature = _basis_signature(
            observed_at=inventory_document["observed_at"],
            inventory_observation_id=inventory_document["observation_id"],
            target_signature=observed_target_signature,
            resources=resources,
            claims=claims,
            observations=current_observations,
            evidence_handles=evidence_handles,
            provenance_handles=provenance_handles,
        )
    finally:
        connection.close()
    return {
        "status": "observed",
        "basis_id": _basis_id(signature),
        "basis_signature": signature,
        "observed_at": inventory_document["observed_at"],
        "target_signature": observed_target_signature,
        "resource_handles": resource_handles,
        "resources": resources,
        "claims": claims,
        "observations": current_observations,
        "evidence_handles": evidence_handles,
        "provenance_handles": provenance_handles,
        "source_handles": source_handles,
    }


def _latest_observations(connection: sqlite3.Connection) -> dict[tuple[str, str], sqlite3.Row]:
    """Most recent per-resource observation of each type, keyed by (handle, type)."""
    rows = connection.execute(
        """
        SELECT o.subject_handle, o.observation_type, o.observation_id, o.evidence_id
        FROM observations AS o
        JOIN (
            SELECT MAX(rowid) AS latest FROM observations
            WHERE producer = 'substrate.resource_inventory'
              AND observation_type != 'resource_inventory'
            GROUP BY subject_handle, observation_type
        ) AS m ON o.rowid = m.latest
        """
    ).fetchall()
    return {(row["subject_handle"], row["observation_type"]): row for row in rows}


def _observe(
    connection: sqlite3.Connection,
    latest: dict[tuple[str, str], sqlite3.Row],
    *,
    handle: str,
    observation_type: str,
    evidence_id: str,
    observed_at: str,
) -> tuple[str, bool]:
    """Reuse the latest observation when its evidence is identical (nothing new was
    observed); otherwise record a new one. Returns (observation_id, reused)."""
    previous = latest.get((handle, observation_type))
    if previous is not None and previous["evidence_id"] == evidence_id:
        return previous["observation_id"], True
    observation_id = _uuid_handle("observation")
    connection.execute(
        """
        INSERT INTO observations
            (observation_id, producer, observed_at, subject_handle,
             observation_type, data_json, evidence_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (observation_id, "substrate.resource_inventory", observed_at, handle,
         observation_type, _FROM_EVIDENCE, evidence_id),
    )
    # "concerns resource" and "supported_by evidence" are derived from this row's columns
    # when read (_node_relations); storing them would duplicate the row.
    return observation_id, False


def _prune_superseded(connection: sqlite3.Connection, claim_cutoff: int) -> None:
    """Drop derived rows of superseded snapshots: claims (re-derived every snapshot) and
    all but the last few inventories. Resources, versions and change evidence stay."""
    connection.execute(
        """
        DELETE FROM relations WHERE subject_type = 'claim' AND subject_id IN
            (SELECT claim_id FROM claims WHERE rowid <= ?)
        """,
        (claim_cutoff,),
    )
    connection.execute("DELETE FROM claims WHERE rowid <= ?", (claim_cutoff,))
    stale = [row[0] for row in connection.execute(
        """
        SELECT observation_id FROM observations WHERE observation_type = 'resource_inventory'
        ORDER BY rowid DESC LIMIT -1 OFFSET ?
        """,
        (_RETAINED_SNAPSHOTS,),
    )]
    for chunk in _chunked(stale):
        placeholders = ", ".join("?" for _ in chunk)
        connection.execute(f"DELETE FROM relations WHERE subject_id IN ({placeholders})", tuple(chunk))
        connection.execute(f"DELETE FROM observations WHERE observation_id IN ({placeholders})", tuple(chunk))
    connection.execute(
        """
        DELETE FROM epistemic_evidence WHERE kind = 'resource_inventory'
          AND evidence_id NOT IN (SELECT evidence_id FROM observations)
        """
    )
    connection.execute(
        """
        DELETE FROM epistemic_evidence WHERE kind = 'observation_set'
          AND evidence_id NOT IN (SELECT object_id FROM relations WHERE predicate = 'derived_from_set')
        """
    )


def refresh(context: InstanceContext) -> dict:
    observed_at = _now()
    connection = storage.connect(context)
    try:
        known = _known_versions(connection)
        latest = _latest_observations(connection)
    finally:
        connection.close()
    stats: dict = {}
    records = _resource_records(context, known, stats)
    inventory_limitations = _inventory_limitations(records)
    if stats.get("snapshot_ignored_files"):
        inventory_limitations.append(
            f"{stats['snapshot_ignored_files']} files matched .tools/snapshot-ignore and were not recorded"
        )
    connection = storage.connect(context)
    try:
        with connection:
            inventory_observation = _uuid_handle("observation")
            current: list[str] = []
            reused = 0
            text_observations: list[tuple[str, str]] = []
            domain_observations: list[dict] = []
            last_digest = None
            for record in records:
                # Evidence is addressed by content, not by observation time: an unchanged
                # resource yields the same digest, the same evidence row, the same version,
                # and (via _observe) the same observation on every refresh.
                evidence_id = _insert_evidence(
                    connection,
                    kind="resource_version",
                    body={
                        "producer": "substrate.resource_inventory",
                        "resource": record,
                    },
                    created_at=observed_at,
                )
                last_digest = record.get("content_hash") or evidence_id.removeprefix("evidence:")
                existing_by_path = connection.execute(
                    "SELECT resource_id, first_seen_at FROM resources WHERE path = ?",
                    (record["path"],),
                ).fetchone()
                resource_id = existing_by_path["resource_id"] if existing_by_path else _resource_id(record["path"])
                version_id = _version_id(record["handle"], evidence_id, record.get("mtime_ns"))
                first_seen = existing_by_path["first_seen_at"] if existing_by_path else observed_at
                connection.execute(
                    """
                    INSERT INTO resources
                        (resource_id, handle, path, kind, first_seen_at, last_seen_at,
                         latest_version_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(resource_id) DO UPDATE SET
                        handle = excluded.handle,
                        path = excluded.path,
                        kind = excluded.kind,
                        last_seen_at = excluded.last_seen_at,
                        latest_version_id = excluded.latest_version_id
                    """,
                    (
                        resource_id,
                        record["handle"],
                        record["path"],
                        record["kind"],
                        first_seen,
                        observed_at,
                        version_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO resource_versions
                        (version_id, resource_id, observed_at, kind, content_hash,
                         size_bytes, mtime_ns, evidence_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        resource_id,
                        observed_at,
                        record["kind"],
                        record.get("content_hash"),
                        record.get("size_bytes"),
                        record.get("mtime_ns"),
                        evidence_id,
                    ),
                )
                # "version_of" and "supported_by" are derived from the version row itself.
                observation_id, was_reused = _observe(
                    connection, latest, handle=record["handle"],
                    observation_type=_observation_type(record), evidence_id=evidence_id,
                    observed_at=observed_at,
                )
                current.append(observation_id)
                reused += was_reused
                if record["text_like"]:
                    text_observations.append((observation_id, evidence_id))
                if (
                    record["domain"]["categories"]
                    or record["domain"]["signals"]
                    or record["domain"]["limitations"]
                ):
                    domain_evidence = _insert_evidence(
                        connection,
                        kind="domain_signal",
                        # No subject in the body: identical profiles share one row.
                        body={"producer": "substrate.domain_signals", "domain": record["domain"]},
                        created_at=observed_at,
                    )
                    domain_observation_id, was_reused = _observe(
                        connection, latest, handle=record["handle"], observation_type="domain_signal",
                        evidence_id=domain_evidence, observed_at=observed_at,
                    )
                    current.append(domain_observation_id)
                    reused += was_reused
                    domain_observations.append(
                        {
                            "observation_id": domain_observation_id,
                            "evidence_id": domain_evidence,
                            "resource_handle": record["handle"],
                            **record["domain"],
                        }
                    )

            # The inventory is written last and names this snapshot's exact observation
            # set (reused and new), which is what the awareness basis reads back.
            inventory_evidence = _insert_evidence(
                connection,
                kind="resource_inventory",
                body={
                    "producer": "substrate.resource_inventory",
                    "target": "path:.",
                    "resource_count": len(records),
                    "handles": [record["handle"] for record in records],
                    "observations": current,
                    "limitations": inventory_limitations,
                },
                created_at=observed_at,
            )
            connection.execute(
                """
                INSERT INTO observations
                    (observation_id, producer, observed_at, subject_handle,
                     observation_type, data_json, evidence_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    inventory_observation,
                    "substrate.resource_inventory",
                    observed_at,
                    "path:.",
                    "resource_inventory",
                    json.dumps(
                        {
                            "resource_count": len(records),
                            "limitations": inventory_limitations,
                            "unknown": "anything not observed by this refresh remains unknown",
                        },
                        sort_keys=True,
                    ),
                    inventory_evidence,
                ),
            )
            claim_cutoff = connection.execute("SELECT COALESCE(MAX(rowid), 0) FROM claims").fetchone()[0]
            claim_count = 0
            if not records:
                _insert_claim(
                    connection,
                    claim_type="target_empty",
                    statement="target observed empty during explicit substrate refresh",
                    derivation_method="deterministic.resource_count",
                    confidence=1.0,
                    data={
                        "resource_count": 0,
                        "domain_profile": "empty_or_nascent",
                    },
                    observation_ids=[inventory_observation],
                    created_at=observed_at,
                )
                claim_count = 1
            else:
                if text_observations:
                    _insert_claim(
                        connection,
                        claim_type="target_has_text_files",
                        statement="target contains text-like files observed by deterministic refresh",
                        derivation_method="deterministic.suffix_classification",
                        confidence=0.8,
                        data={"text_like_file_count": len(text_observations)},
                        observation_ids=[observation for observation, _ in text_observations],
                        created_at=observed_at,
                    )
                    claim_count += 1
                claim_count += _insert_domain_claims(
                    connection,
                    observations=domain_observations,
                    created_at=observed_at,
                )
            _prune_superseded(connection, claim_cutoff)
        connection.execute("PRAGMA incremental_vacuum").fetchall()  # hand pruned pages back to the OS
    finally:
        connection.close()
    return {
        "ok": True,
        "observed": {
            "resource_count": len(records),
            "observation_count": len(current) + 1,
            "observations_reused": reused,
            "claim_count": claim_count,
            "digest": last_digest,
            "target_signature": _resource_signature(records),
            "limitations": inventory_limitations,
            "unknown": "anything not observed by this refresh remains unknown",
        },
    }


def list_resources(context: InstanceContext, limit: int = 100) -> list[dict]:
    connection = storage.connect(context)
    try:
        rows = connection.execute(
            """
            SELECT resource_id, handle, path, kind, first_seen_at, last_seen_at,
                   latest_version_id
            FROM resources
            ORDER BY path
            LIMIT ?
            """,
            (_bounded_limit(limit),),
        ).fetchall()
    finally:
        connection.close()
    return [_row_to_dict(row) for row in rows]


def read_resource(context: InstanceContext, handle: str) -> dict:
    connection = storage.connect(context)
    try:
        row = connection.execute(
            """
            SELECT resource_id, handle, path, kind, first_seen_at, last_seen_at,
                   latest_version_id
            FROM resources
            WHERE resource_id = ? OR handle = ?
            """,
            (handle, handle),
        ).fetchone()
        if row is None:
            raise SubstrateError(f"resource not found: {handle}")
        resource = _row_to_dict(row)
        if resource["latest_version_id"]:
            version = connection.execute(
                """
                SELECT version_id, resource_id, observed_at, kind, content_hash,
                       size_bytes, mtime_ns, evidence_id
                FROM resource_versions
                WHERE version_id = ?
                """,
                (resource["latest_version_id"],),
            ).fetchone()
            resource["latest"] = _row_to_dict(version) if version else None
    finally:
        connection.close()
    return resource


def list_versions(context: InstanceContext, resource_handle: str | None = None) -> list[dict]:
    connection = storage.connect(context)
    try:
        if resource_handle:
            resource = connection.execute(
                "SELECT resource_id FROM resources WHERE resource_id = ? OR handle = ?",
                (resource_handle, resource_handle),
            ).fetchone()
            resource_id = resource["resource_id"] if resource else resource_handle
            rows = connection.execute(
                """
                SELECT version_id, resource_id, observed_at, kind, content_hash,
                       size_bytes, mtime_ns, evidence_id
                FROM resource_versions
                WHERE resource_id = ?
                ORDER BY rowid
                """,
                (resource_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT version_id, resource_id, observed_at, kind, content_hash,
                       size_bytes, mtime_ns, evidence_id
                FROM resource_versions
                ORDER BY rowid
                LIMIT 100
                """
            ).fetchall()
    finally:
        connection.close()
    return [_row_to_dict(row) for row in rows]


def read_version(context: InstanceContext, version_id: str) -> dict:
    connection = storage.connect(context)
    try:
        row = connection.execute(
            """
            SELECT version_id, resource_id, observed_at, kind, content_hash,
                   size_bytes, mtime_ns, evidence_id
            FROM resource_versions
            WHERE version_id = ?
            """,
            (version_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise SubstrateError(f"version not found: {version_id}")
    return _row_to_dict(row)


def list_observations(context: InstanceContext, limit: int = 100) -> list[dict]:
    connection = storage.connect(context)
    try:
        rows = connection.execute(
            """
            SELECT observation_id, producer, observed_at, subject_handle, observation_type,
                   data_json, evidence_id
            FROM observations
            ORDER BY rowid
            LIMIT ?
            """,
            (_bounded_limit(limit),),
        ).fetchall()
        return _decode_observations(connection, rows)
    finally:
        connection.close()


def read_observation(context: InstanceContext, observation_id: str) -> dict:
    connection = storage.connect(context)
    try:
        row = connection.execute(
            """
            SELECT observation_id, producer, observed_at, subject_handle, observation_type,
                   data_json, evidence_id
            FROM observations
            WHERE observation_id = ?
            """,
            (observation_id,),
        ).fetchone()
        if row is None:
            raise SubstrateError(f"observation not found: {observation_id}")
        return _decode_observations(connection, [row])[0]
    finally:
        connection.close()


def read_evidence(context: InstanceContext, evidence_id: str) -> dict:
    connection = storage.connect(context)
    try:
        row = connection.execute(
            """
            SELECT evidence_id, digest, created_at, kind, media_type, body_json
            FROM epistemic_evidence
            WHERE evidence_id = ?
            """,
            (evidence_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise SubstrateError(f"epistemic evidence not found: {evidence_id}")
    document = _row_to_dict(row)
    document["body"] = _unpack(document.pop("body_json"))
    return document


def _evidence_bodies(connection: sqlite3.Connection, evidence_ids: list[str]) -> dict[str, Any]:
    bodies: dict[str, Any] = {}
    for chunk in _chunked(sorted(set(evidence_ids))):
        placeholders = ", ".join("?" for _ in chunk)
        for row in connection.execute(
            f"SELECT evidence_id, body_json FROM epistemic_evidence WHERE evidence_id IN ({placeholders})",
            tuple(chunk),
        ):
            bodies[row["evidence_id"]] = _unpack(row["body_json"])
    return bodies


def _decode_observations(connection: sqlite3.Connection, rows: list) -> list[dict]:
    """Decode observation rows, rebuilding data stored by reference to its evidence."""
    documents = [_row_to_dict(row) for row in rows]
    bodies = _evidence_bodies(connection, [d["evidence_id"] for d in documents
                                           if d.get("data_json") == _FROM_EVIDENCE])
    for document in documents:
        raw = document.pop("data_json")
        if raw != _FROM_EVIDENCE:
            document["data"] = _unpack(raw)
            continue
        body = bodies.get(document["evidence_id"], {})
        if document["observation_type"] == "domain_signal":
            handle = document["subject_handle"]
            relative = handle.removeprefix("path:")
            relative = relative[:-1] if relative.endswith("/") and relative != "/" else relative
            document["data"] = {"path": relative, "handle": handle, **body.get("domain", {})}
        else:
            document["data"] = body.get("resource", {})
    return documents


def list_claims(context: InstanceContext, limit: int = 100) -> list[dict]:
    connection = storage.connect(context)
    try:
        rows = connection.execute(
            """
            SELECT claim_id, created_at, claim_type, statement, derivation_method,
                   confidence, data_json
            FROM claims
            ORDER BY rowid
            LIMIT ?
            """,
            (_bounded_limit(limit),),
        ).fetchall()
    finally:
        connection.close()
    return [_decode(row) for row in rows]


def read_claim(context: InstanceContext, claim_id: str) -> dict:
    connection = storage.connect(context)
    try:
        row = connection.execute(
            """
            SELECT claim_id, created_at, claim_type, statement, derivation_method,
                   confidence, data_json
            FROM claims
            WHERE claim_id = ?
            """,
            (claim_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise SubstrateError(f"claim not found: {claim_id}")
    return _decode(row)


def read_relation(context: InstanceContext, relation_handle: str) -> dict:
    raw = relation_handle.removeprefix("relation:")
    if ">" in raw:  # a derived relation: subject>predicate>object
        subject_id = raw.split(">", 1)[0]
        connection = storage.connect(context)
        try:
            for relation in _node_relations(connection, subject_id):
                if relation["relation_id"] == raw:
                    return {**relation, "handle": f"relation:{raw}"}
        finally:
            connection.close()
        raise SubstrateError(f"relation not found: {relation_handle}")
    relation_id = _relation_number(relation_handle)
    connection = storage.connect(context)
    try:
        row = connection.execute(
            """
            SELECT relation_id, created_at, subject_type, subject_id, predicate,
                   object_type, object_id
            FROM relations
            WHERE relation_id = ?
            """,
            (relation_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise SubstrateError(f"relation not found: {relation_handle}")
    relation = _row_to_dict(row)
    relation["handle"] = f"relation:{relation['relation_id']}"
    return relation


def _virtual(created_at: str, subject_type: str, subject_id: str, predicate: str,
             object_type: str, object_id: str) -> dict:
    return {"relation_id": f"{subject_id}>{predicate}>{object_id}", "created_at": created_at,
            "subject_type": subject_type, "subject_id": subject_id, "predicate": predicate,
            "object_type": object_type, "object_id": object_id}


def _node_relations(connection: sqlite3.Connection, subject_id: str) -> list[dict]:
    """Outgoing relations of a node: stored ones, plus those derived from row columns
    (observation/version links) and expanded claim observation sets."""
    relations: list[dict] = []
    for row in connection.execute(
        """
        SELECT relation_id, created_at, subject_type, subject_id, predicate, object_type, object_id
        FROM relations WHERE subject_id = ? ORDER BY relation_id
        """,
        (subject_id,),
    ):
        relation = _row_to_dict(row)
        if relation["predicate"] != "derived_from_set":
            relations.append(relation)
            continue
        members = _evidence_bodies(connection, [relation["object_id"]]).get(relation["object_id"], {})
        relations.append(relation)
        relations.extend(_virtual(relation["created_at"], "claim", subject_id, "derived_from",
                                  "observation", member) for member in members.get("observations", []))
    kind = subject_id.split(":", 1)[0]
    if kind == "observation":
        row = connection.execute(
            """
            SELECT o.observed_at, o.observation_type, o.evidence_id, r.resource_id
            FROM observations AS o LEFT JOIN resources AS r ON r.handle = o.subject_handle
            WHERE o.observation_id = ?
            """,
            (subject_id,),
        ).fetchone()
        if row is not None:
            if row["resource_id"] and row["observation_type"] != "resource_inventory":
                relations.append(_virtual(row["observed_at"], "observation", subject_id, "concerns",
                                          "resource", row["resource_id"]))
            relations.append(_virtual(row["observed_at"], "observation", subject_id, "supported_by",
                                      "evidence", row["evidence_id"]))
    elif kind == "version":
        row = connection.execute(
            "SELECT observed_at, resource_id, evidence_id FROM resource_versions WHERE version_id = ?",
            (subject_id,),
        ).fetchone()
        if row is not None:
            relations.append(_virtual(row["observed_at"], "version", subject_id, "version_of",
                                      "resource", row["resource_id"]))
            relations.append(_virtual(row["observed_at"], "version", subject_id, "supported_by",
                                      "evidence", row["evidence_id"]))
    return relations


def trace(context: InstanceContext, start_id: str) -> dict:
    connection = storage.connect(context)
    try:
        start = _load_node(connection, start_id)
        nodes: dict[str, dict] = {start_id: start}
        relations: list[dict] = []
        frontier = [start_id]
        seen = {start_id}
        while frontier:
            subject_id = frontier.pop(0)
            for relation in _node_relations(connection, subject_id):
                relations.append(relation)
                object_id = relation["object_id"]
                if object_id not in nodes:
                    nodes[object_id] = _load_node(connection, object_id, relation["object_type"])
                if object_id not in seen:
                    seen.add(object_id)
                    frontier.append(object_id)
        return {"start": start, "nodes": list(nodes.values()), "relations": relations}
    finally:
        connection.close()


def _load_node(
    connection: sqlite3.Connection,
    identifier: str,
    kind: str | None = None,
) -> dict[str, Any]:
    if kind is None:
        kind = identifier.split(":", 1)[0]
        if kind == "path":
            kind = "resource"
    table_sql = {
        "resource": (
            "resources",
            "SELECT resource_id AS id, 'resource' AS type, handle, path, kind FROM resources "
            "WHERE resource_id = ? OR handle = ?",
        ),
        "version": (
            "resource_versions",
            "SELECT version_id AS id, 'version' AS type, resource_id, kind, content_hash, "
            "evidence_id FROM resource_versions WHERE version_id = ?",
        ),
        "observation": (
            "observations",
            "SELECT observation_id AS id, 'observation' AS type, observation_type, "
            "subject_handle, evidence_id FROM observations WHERE observation_id = ?",
        ),
        "evidence": (
            "epistemic_evidence",
            "SELECT evidence_id AS id, 'evidence' AS type, kind, digest FROM epistemic_evidence "
            "WHERE evidence_id = ?",
        ),
        "claim": (
            "claims",
            "SELECT claim_id AS id, 'claim' AS type, claim_type, statement FROM claims "
            "WHERE claim_id = ?",
        ),
        "relation": (
            "relations",
            "SELECT relation_id AS id, 'relation' AS type, subject_type, subject_id, "
            "predicate, object_type, object_id FROM relations WHERE relation_id = ?",
        ),
    }
    if kind not in table_sql:
        raise SubstrateError(f"unsupported trace node type: {kind}")
    _, sql = table_sql[kind]
    if kind == "resource":
        parameters: tuple[str | int, ...] = (identifier, identifier)
    elif kind == "relation":
        if ">" in identifier:
            subject_id = identifier.removeprefix("relation:").split(">", 1)[0]
            for relation in _node_relations(connection, subject_id):
                if relation["relation_id"] == identifier.removeprefix("relation:"):
                    return {"id": relation["relation_id"], "type": "relation", **relation}
            raise SubstrateError(f"trace node not found: {identifier}")
        parameters = (_relation_number(identifier),)
    else:
        parameters = (identifier,)
    row = connection.execute(sql, parameters).fetchone()
    if row is None:
        raise SubstrateError(f"trace node not found: {identifier}")
    return _row_to_dict(row)


def _relation_number(relation_handle: str) -> int:
    prefix, separator, value = relation_handle.partition(":")
    if prefix != "relation" or not separator or not value.isdecimal():
        raise SubstrateError(f"invalid relation handle: {relation_handle}")
    return int(value)


def _current_refresh_observations(
    connection: sqlite3.Connection,
    *,
    inventory_row: int,
    inventory_observation_id: str,
    resource_handles: list[str],
) -> list[dict]:
    rows = connection.execute(
        """
        SELECT observation_id, producer, observed_at, subject_handle, observation_type,
               data_json, evidence_id
        FROM observations
        WHERE rowid >= ?
          AND producer = 'substrate.resource_inventory'
        ORDER BY rowid
        """,
        (inventory_row,),
    ).fetchall()
    allowed_subjects = set(resource_handles)
    observations: list[dict] = []
    for document in _decode_observations(connection, rows):
        if document["observation_id"] == inventory_observation_id:
            observations.append(document)
        elif document["subject_handle"] in allowed_subjects:
            observations.append(document)
    return observations


def _observations_by_id(connection: sqlite3.Connection, observation_ids: list[str]) -> list[dict]:
    rows: list = []
    for chunk in _chunked(observation_ids):
        placeholders = ", ".join("?" for _ in chunk)
        rows += connection.execute(
            f"""
            SELECT observation_id, producer, observed_at, subject_handle, observation_type,
                   data_json, evidence_id
            FROM observations
            WHERE observation_id IN ({placeholders})
            """,
            tuple(chunk),
        ).fetchall()
    by_id = {document["observation_id"]: document for document in _decode_observations(connection, rows)}
    return [by_id[i] for i in observation_ids if i in by_id]


def _current_refresh_resources(
    connection: sqlite3.Connection,
    resource_handles: list[str],
) -> list[dict]:
    if not resource_handles:
        return []
    rows = []
    for chunk in _chunked(resource_handles):
        placeholders = ", ".join("?" for _ in chunk)
        rows += connection.execute(
            f"""
            SELECT resource_id, handle, path, kind, first_seen_at, last_seen_at,
                   latest_version_id
            FROM resources
            WHERE handle IN ({placeholders})
            """,
            tuple(chunk),
        ).fetchall()
    return sorted((_row_to_dict(row) for row in rows), key=lambda row: row["path"])


def _claims_for_snapshot(connection: sqlite3.Connection, observed_at: str) -> list[dict]:
    """Claims derived by the snapshot taken at observed_at (claims carry its timestamp)."""
    rows = connection.execute(
        """
        SELECT claim_id, created_at, claim_type, statement, derivation_method, confidence, data_json
        FROM claims WHERE created_at = ? ORDER BY rowid
        """,
        (observed_at,),
    ).fetchall()
    return [_decode(row) for row in rows]


def _relations_for_basis(
    connection: sqlite3.Connection,
    *,
    observations: list[dict],
    resources: list[dict],
    claim_ids: list[str],
) -> list[dict]:
    """Provenance of the basis: derived observation links built from rows already in
    hand, plus stored (and expanded) claim relations."""
    resource_by_handle = {resource["handle"]: resource["resource_id"] for resource in resources}
    relations: list[dict] = []
    for item in observations:
        observation_id = item["observation_id"]
        resource_id = resource_by_handle.get(item["subject_handle"])
        if resource_id and item["observation_type"] != "resource_inventory":
            relations.append(_virtual(item["observed_at"], "observation", observation_id, "concerns",
                                      "resource", resource_id))
        relations.append(_virtual(item["observed_at"], "observation", observation_id, "supported_by",
                                  "evidence", item["evidence_id"]))
    for claim_id in claim_ids:
        relations.extend(_node_relations(connection, claim_id))
    return sorted(relations, key=lambda relation: str(relation["relation_id"]))


def _basis_signature(
    *,
    observed_at: str,
    inventory_observation_id: str,
    target_signature: str,
    resources: list[dict],
    claims: list[dict],
    observations: list[dict],
    evidence_handles: list[str],
    provenance_handles: list[str],
) -> str:
    payload = {
        "observed_at": observed_at,
        "inventory_observation_id": inventory_observation_id,
        "target_signature": target_signature,
        "resources": [
            {
                "handle": item["handle"],
                "latest_version_id": item.get("latest_version_id"),
            }
            for item in resources
        ],
        "claims": [
            {
                "claim_id": item["claim_id"],
                "claim_type": item["claim_type"],
                "statement": item["statement"],
            }
            for item in claims
        ],
        "observations": [
            {
                "observation_id": item["observation_id"],
                "subject_handle": item["subject_handle"],
                "observation_type": item["observation_type"],
                "evidence_id": item["evidence_id"],
            }
            for item in observations
        ],
        "evidence_handles": evidence_handles,
        "provenance_handles": provenance_handles,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _bounded_limit(limit: int) -> int:
    return max(1, min(int(limit), 500))

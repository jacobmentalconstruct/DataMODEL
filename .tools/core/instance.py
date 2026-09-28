from __future__ import annotations

import json
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .constants import INSTANCE_SCHEMA_VERSION, PRODUCT_VERSION

MANIFEST_NAME = "instance.json"


class InstanceError(RuntimeError):
    """Canonical identity is absent, malformed, or structurally untrue."""


@dataclass(frozen=True)
class InstanceContext:
    instance_root: Path
    target_root: Path
    state_root: Path
    logs_root: Path
    instance_uuid: str
    schema_version: int
    product_version: str
    created_at: str
    target_relation: str

def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise InstanceError("instance_uuid must be a canonical UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise InstanceError("instance_uuid must be a canonical UUID string") from exc
    canonical = str(parsed)
    if value != canonical:
        raise InstanceError("instance_uuid must use canonical lowercase UUID form")
    return canonical


def _validate(instance_root: Path, document: object) -> InstanceContext:
    if not isinstance(document, dict):
        raise InstanceError("instance.json must contain a JSON object")

    required = {
        "schema_version",
        "instance_uuid",
        "target_relation",
        "created_at",
        "product_version",
    }
    missing = sorted(required - set(document))
    if missing:
        raise InstanceError(f"instance.json is missing fields: {', '.join(missing)}")

    schema = document["schema_version"]
    if not isinstance(schema, int) or isinstance(schema, bool):
        raise InstanceError("schema_version must be an integer")
    if schema != INSTANCE_SCHEMA_VERSION:
        raise InstanceError(
            f"unsupported instance schema {schema}; runtime supports {INSTANCE_SCHEMA_VERSION}"
        )

    relation = document["target_relation"]
    if relation != "..":
        raise InstanceError("target_relation must be '..' for a direct-child installation")

    target_root = (instance_root / relation).resolve()
    if not target_root.is_dir():
        raise InstanceError("target_relation does not resolve to an existing directory")
    if instance_root.parent != target_root:
        raise InstanceError("the installed instance must be a direct child of its target")

    created_at = document["created_at"]
    if not isinstance(created_at, str):
        raise InstanceError("created_at must be an ISO-8601 string")
    try:
        parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InstanceError("created_at must be an ISO-8601 string") from exc
    if parsed_created_at.tzinfo is None:
        raise InstanceError("created_at must include a timezone")

    product_version = document["product_version"]
    if not isinstance(product_version, str) or not product_version.strip():
        raise InstanceError("product_version must be a non-empty string")

    return InstanceContext(
        instance_root=instance_root,
        target_root=target_root,
        state_root=instance_root / "state",
        logs_root=instance_root / "logs",
        instance_uuid=_canonical_uuid(document["instance_uuid"]),
        schema_version=schema,
        product_version=product_version,
        created_at=created_at,
        target_relation=relation,
    )


# Instance-private paths: identity and history of *this* copy. A packaged copy must not
# carry them, or every project unpacked from it would share one identity and one history.
PRIVATE = (MANIFEST_NAME, "state", "logs")
_NEVER_PACKED = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git"}
_UNPACK_BAT = """@echo off
rem Unpack {name}.zip into a folder you choose (needs Python 3).
where python >nul 2>nul && (python "%~dp0{name}.zip" %*) || (py -3 "%~dp0{name}.zip" %*)
pause
"""
_UNPACK_SH = """#!/bin/sh
# Unpack {name}.zip into a folder you choose (needs Python 3).
here="$(cd "$(dirname "$0")" && pwd)"
if command -v python3 >/dev/null 2>&1; then exec python3 "$here/{name}.zip" "$@"; fi
exec python "$here/{name}.zip" "$@"
"""


def default_archive(instance_root: str | Path) -> Path:
    """<parent of project>/<project name without leading dots>.zip"""
    target = Path(instance_root).resolve().parent
    return target.parent / f"{target.name.lstrip('.') or 'project'}.zip"


def package(instance_root: str | Path, destination: str | Path) -> dict:
    """Zip the project except this instance's private paths, caches and .git; the archive
    unpacks itself (`python <name>.zip`) and gets double-clickable launchers beside it."""
    instance_path = Path(instance_root).resolve()
    target_path = instance_path.parent
    destination = Path(destination).resolve()
    if destination.suffix.lower() != ".zip":
        raise InstanceError("package destination must be a .zip file")
    if target_path == destination.parent or target_path in destination.parents:
        raise InstanceError("put the archive outside the project, or it would pack itself")
    private = {(instance_path / name).resolve() for name in PRIVATE}
    files = 0
    size = 0
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(instance_path / "bin" / "unpack.py", "__main__.py")
        for path in sorted(target_path.rglob("*")):
            if path == destination or any(part in _NEVER_PACKED for part in path.parts):
                continue
            resolved = path.resolve()
            if any(resolved == p or p in resolved.parents for p in private):
                continue
            if path.is_file():
                # Flat: the project's contents sit at the zip root, so the archive can be
                # extracted straight into an existing folder of files.
                archive.write(path, path.relative_to(target_path))
                files += 1
                size += path.stat().st_size
    name = destination.stem
    launchers = [destination.with_name(f"{name}-unpack.bat"), destination.with_name(f"{name}-unpack.sh")]
    launchers[0].write_text(_UNPACK_BAT.format(name=name), encoding="utf-8", newline="\r\n")
    launchers[1].write_text(_UNPACK_SH.format(name=name), encoding="utf-8", newline="\n")
    launchers[1].chmod(0o755)
    return {"ok": True, "archive": str(destination), "files": files, "bytes": size,
            "launchers": [str(path) for path in launchers],
            "excluded": [f"{instance_path.name}/{name}" for name in PRIVATE] + [".git", "caches"]}


def create(instance_root: str | Path, target_root: str | Path) -> InstanceContext:
    instance_path = Path(instance_root).resolve()
    target_path = Path(target_root).resolve()
    if not instance_path.is_dir():
        raise InstanceError("instance root must already exist")
    if not target_path.is_dir():
        raise InstanceError("target root must be an existing directory")
    if instance_path.parent != target_path:
        raise InstanceError("an instance must be created directly inside its target")

    manifest_path = instance_path / MANIFEST_NAME
    if manifest_path.exists():
        raise InstanceError("instance identity already exists; refusing to replace it")

    document = {
        "schema_version": INSTANCE_SCHEMA_VERSION,
        "instance_uuid": str(uuid.uuid4()),
        "target_relation": "..",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "product_version": PRODUCT_VERSION,
    }
    manifest_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return _validate(instance_path, document)


def load(instance_root: str | Path, *, create_missing: bool = False) -> InstanceContext:
    """Load this instance's identity; with create_missing, a fresh copy initialises itself."""
    instance_path = Path(instance_root).resolve()
    manifest_path = instance_path / MANIFEST_NAME
    if not manifest_path.is_file():
        if create_missing:
            return create(instance_path, instance_path.parent)
        raise InstanceError(
            f"no canonical instance identity at {manifest_path}; "
            "initialise once with: python .tools/bin/helpers.py init"
        )
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstanceError(f"instance.json is unreadable or invalid JSON: {exc}") from exc
    return _validate(instance_path, document)

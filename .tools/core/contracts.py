from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .constants import TOOL_CONTRACT_VERSION


class ContractError(RuntimeError):
    pass


_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_AUTHORITIES = {"observe", "sandbox", "apply"}
_DOMAINS = {"target", "instance", "state"}
_MANIFEST_FIELDS = {
    "contract_version",
    "id",
    "description",
    "authority",
    "input_schema",
    "output_schema",
    "reads",
    "writes",
    "applicability",
    "path_arguments",
    "invocation",
}


@dataclass(frozen=True)
class ToolManifest:
    id: str
    description: str
    authority: str
    input_schema: dict
    output_schema: dict
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    applicability: dict
    path_arguments: dict[str, str]
    entry: Path
    digest: str


def _string_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError(f"{field} must be an array of domain strings")
    result = tuple(value)
    unknown = sorted(set(result) - _DOMAINS)
    if unknown:
        raise ContractError(f"{field} contains unknown domains: {', '.join(unknown)}")
    if len(set(result)) != len(result):
        raise ContractError(f"{field} must not contain duplicate domains")
    return result


def parse_manifest(raw: str, source: Path, instance_root: Path) -> ToolManifest:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON in {source}: {exc}") from exc
    if not isinstance(document, dict):
        raise ContractError(f"manifest must be a JSON object: {source}")
    missing = sorted(_MANIFEST_FIELDS - set(document))
    extra = sorted(set(document) - _MANIFEST_FIELDS)
    if missing:
        raise ContractError(f"manifest {source} is missing: {', '.join(missing)}")
    if extra:
        raise ContractError(f"manifest {source} has unknown fields: {', '.join(extra)}")
    if document["contract_version"] != TOOL_CONTRACT_VERSION:
        raise ContractError(
            f"manifest {source} uses unsupported contract version "
            f"{document['contract_version']!r}"
        )

    tool_id = document["id"]
    if not isinstance(tool_id, str) or not _ID_PATTERN.fullmatch(tool_id):
        raise ContractError(f"manifest id is invalid in {source}")
    if source.parent.name != tool_id:
        raise ContractError(f"manifest id {tool_id!r} does not match directory {source.parent.name!r}")

    description = document["description"]
    if not isinstance(description, str) or not description.strip():
        raise ContractError(f"manifest description is empty in {source}")
    authority = document["authority"]
    if authority not in _AUTHORITIES:
        raise ContractError(f"manifest authority is invalid in {source}: {authority!r}")

    input_schema = document["input_schema"]
    output_schema = document["output_schema"]
    if not isinstance(input_schema, dict) or not isinstance(output_schema, dict):
        raise ContractError(f"manifest schemas must be objects in {source}")
    if input_schema.get("type") != "object" or output_schema.get("type") != "object":
        raise ContractError(f"manifest schemas must describe JSON objects in {source}")

    reads = _string_list(document["reads"], "reads")
    writes = _string_list(document["writes"], "writes")
    if authority == "observe" and writes:
        raise ContractError(f"observe tool {tool_id!r} cannot declare write domains")
    applicability = document["applicability"]
    if not isinstance(applicability, dict):
        raise ContractError(f"applicability must be an object in {source}")

    path_arguments = document["path_arguments"]
    if not isinstance(path_arguments, dict):
        raise ContractError(f"path_arguments must be an object in {source}")
    properties = input_schema.get("properties", {})
    for name, domain in path_arguments.items():
        if not isinstance(name, str) or name not in properties:
            raise ContractError(f"path argument {name!r} is not declared by input_schema")
        if domain not in _DOMAINS:
            raise ContractError(f"path argument {name!r} has unknown domain {domain!r}")
        if domain not in reads and domain not in writes:
            raise ContractError(
                f"path argument {name!r} uses undeclared access domain {domain!r}"
            )

    invocation = document["invocation"]
    if not isinstance(invocation, dict) or set(invocation) != {"kind", "entry"}:
        raise ContractError(f"invocation must contain only kind and entry in {source}")
    if invocation["kind"] != "python":
        raise ContractError(f"unsupported invocation kind in {source}")
    entry_value = invocation["entry"]
    if not isinstance(entry_value, str) or not entry_value:
        raise ContractError(f"invocation entry must be a relative path in {source}")
    entry_rel = Path(entry_value)
    if entry_rel.is_absolute():
        raise ContractError(f"invocation entry must be relative in {source}")
    entry = (instance_root / entry_rel).resolve()
    tools_root = (instance_root / "tools").resolve()
    try:
        entry.relative_to(tools_root)
    except ValueError as exc:
        raise ContractError(f"invocation entry escapes the tools root in {source}") from exc
    if not entry.is_file():
        raise ContractError(f"invocation entry does not exist in {source}: {entry_value}")

    return ToolManifest(
        id=tool_id,
        description=description.strip(),
        authority=authority,
        input_schema=input_schema,
        output_schema=output_schema,
        reads=reads,
        writes=writes,
        applicability=applicability,
        path_arguments=dict(path_arguments),
        entry=entry,
        digest=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )


def _matches_type(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ContractError(f"unsupported JSON schema type: {expected!r}")


def validate_json(value: object, schema: dict, location: str = "$") -> list[str]:
    """Validate the deliberately small JSON Schema subset used by tool manifests."""
    errors: list[str] = []
    expected = schema.get("type")
    if expected is not None:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(_matches_type(value, item) for item in allowed):
            return [f"{location} must be {' or '.join(allowed)}"]

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{location} must be one of {schema['enum']!r}")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                errors.append(f"{location}.{name} is required")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    errors.append(f"{location}.{name} is not allowed")
        for name, child in value.items():
            if name in properties:
                errors.extend(validate_json(child, properties[name], f"{location}.{name}"))

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, child in enumerate(value):
            errors.extend(validate_json(child, schema["items"], f"{location}[{index}]"))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{location} must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{location} must be <= {schema['maximum']}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{location} is shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{location} is longer than {schema['maxLength']} characters")
    return errors

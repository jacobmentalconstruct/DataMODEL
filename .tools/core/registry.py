from __future__ import annotations

from .contracts import ContractError, ToolManifest, parse_manifest
from .instance import InstanceContext


class RegistryError(RuntimeError):
    pass


def discover(context: InstanceContext) -> dict[str, ToolManifest]:
    tools_root = context.instance_root / "tools"
    if not tools_root.is_dir():
        raise RegistryError(f"installed tools directory is missing: {tools_root}")

    records: dict[str, ToolManifest] = {}
    for source in sorted(tools_root.glob("*/manifest.json")):
        if source.parent.name.startswith("_"):
            continue
        try:
            manifest = parse_manifest(source.read_text(encoding="utf-8"), source, context.instance_root)
        except (OSError, ContractError) as exc:
            raise RegistryError(str(exc)) from exc
        if manifest.id in records:
            raise RegistryError(f"duplicate tool id: {manifest.id}")
        records[manifest.id] = manifest
    if not records:
        raise RegistryError("no valid tools are installed")
    return records


def get(context: InstanceContext, tool_id: str) -> ToolManifest:
    records = discover(context)
    try:
        return records[tool_id]
    except KeyError as exc:
        raise RegistryError(f"unknown tool: {tool_id}") from exc

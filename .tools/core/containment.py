from __future__ import annotations

from pathlib import Path

from .contracts import ToolManifest
from .instance import InstanceContext


class ContainmentError(RuntimeError):
    pass


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_path(context: InstanceContext, raw: object, domain: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ContainmentError("declared path arguments must be non-empty strings")
    if "\x00" in raw:
        raise ContainmentError("path contains a null byte")
    supplied = Path(raw)
    if supplied.is_absolute():
        raise ContainmentError("absolute paths are not accepted; use a root-relative path")

    roots = {
        "target": context.target_root,
        "instance": context.instance_root,
        "state": context.state_root,
    }
    root = roots[domain].resolve()
    candidate = (root / supplied).resolve(strict=False)
    if not _within(candidate, root):
        raise ContainmentError(f"path escapes the declared {domain} root: {raw}")

    instance_root = context.instance_root.resolve()
    if domain == "target" and _within(candidate, instance_root):
        raise ContainmentError(
            "target-scoped tools cannot access the instrument's private subtree"
        )
    return candidate


def resolve_declared_paths(
    context: InstanceContext, manifest: ToolManifest, arguments: dict
) -> dict:
    resolved = dict(arguments)
    for name, domain in manifest.path_arguments.items():
        if name not in resolved:
            continue
        value = resolved[name]
        if isinstance(value, list):
            resolved[name] = [str(resolve_path(context, item, domain)) for item in value]
        else:
            resolved[name] = str(resolve_path(context, value, domain))
    return resolved

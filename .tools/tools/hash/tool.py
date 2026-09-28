from __future__ import annotations

import hashlib
from pathlib import Path

from core.tool_runtime import MechanicalContext, run_tool


def run(arguments: dict, context: MechanicalContext) -> dict:
    raw_paths = list(arguments.get("paths") or [])
    if "path" in arguments:
        raw_paths.insert(0, arguments["path"])
    if not raw_paths:
        return {"ok": False, "error": "path or paths is required"}
    lines: list[str] = []
    for raw in raw_paths:
        path = Path(raw)
        relative = context.target_relative(path)
        if not path.is_file():
            lines.append(f"-  {relative}  (not a file)")
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        lines.append(f"{digest.hexdigest()}  {relative}  {path.stat().st_size}")
    return {"ok": True, "text": "\n".join(lines)}


if __name__ == "__main__":
    raise SystemExit(run_tool(run))

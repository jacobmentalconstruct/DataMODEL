from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class MechanicalContext:
    target_root: Path
    excluded_roots: tuple[Path, ...]

    @classmethod
    def from_document(cls, document: object) -> "MechanicalContext":
        if not isinstance(document, dict):
            raise ValueError("mechanical context must be an object")
        required = {"target_root", "excluded_roots"}
        missing = sorted(required - document.keys())
        unknown = sorted(document.keys() - required)
        if missing:
            raise ValueError(f"mechanical context is missing fields: {', '.join(missing)}")
        if unknown:
            raise ValueError(f"mechanical context has unknown fields: {', '.join(unknown)}")

        target_value = document["target_root"]
        excluded_values = document["excluded_roots"]
        if not isinstance(target_value, str) or not Path(target_value).is_absolute():
            raise ValueError("target_root must be an absolute path string")
        if not isinstance(excluded_values, list) or any(
            not isinstance(value, str) or not Path(value).is_absolute()
            for value in excluded_values
        ):
            raise ValueError("excluded_roots must be an array of absolute path strings")

        target_root = Path(target_value).resolve()
        excluded_roots = tuple(Path(value).resolve() for value in excluded_values)
        for root in excluded_roots:
            try:
                root.relative_to(target_root)
            except ValueError as exc:
                raise ValueError("excluded roots must be within target_root") from exc
        return cls(target_root=target_root, excluded_roots=excluded_roots)

    def target_relative(self, path: str | Path) -> str:
        absolute = Path(os.path.abspath(path))
        return absolute.relative_to(self.target_root).as_posix()

    def is_excluded(self, path: str | Path) -> bool:
        candidate = Path(path).resolve(strict=False)
        for root in self.excluded_roots:
            try:
                candidate.relative_to(root)
                return True
            except ValueError:
                continue
        return False


def run_tool(function: Callable[[dict, MechanicalContext], dict]) -> int:
    """Read one JSON request from stdin and emit exactly one JSON result object."""
    try:
        request = json.loads(sys.stdin.read())
        if not isinstance(request, dict) or set(request) != {"args", "context"}:
            raise ValueError("request must contain only args and context")
        if not isinstance(request.get("args"), dict):
            raise ValueError("request must contain an args object")
        context = MechanicalContext.from_document(request.get("context"))
        result = function(request["args"], context)
        if not isinstance(result, dict):
            raise TypeError("tool function must return an object")
        if "ok" not in result:
            result = {"ok": True, **result}
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "error_kind": "tool_exception",
                },
                sort_keys=True,
            )
        )
        return 1

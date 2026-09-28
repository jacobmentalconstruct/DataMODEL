"""Minimal stdio MCP server over the same operations as the CLI.

Context economy is the design goal: the hot tools are exposed directly with small
schemas, everything else sits behind one `run` tool whose `help` op is fetched only when
needed, and results are rendered as plain text rather than duplicated JSON.

Authority is fixed by whoever launches the server (`helpers mcp --authority ...`), never
chosen per call by the client, and tools the session cannot use are not listed at all.
"""
from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from . import operations, registry, render
from .constants import PRODUCT_VERSION
from .instance import InstanceContext

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
# Exposed directly, in this order; everything else is reached through `run`.
HOT_TOOLS = ("changes", "outline", "ls", "grep", "read", "edit", "write")
_RUN_DESCRIPTION = (
    "More tools: map (project card), deps (import graph), refs (defs/uses of a name), "
    "schema (data files), ollama (local models: run/sweep/chat/embed), hash; "
    "plus receipts, journal, substrate, awareness, governed mutation. "
    'op="help" lists all; args.op names one for its contract. '
    "calls=[{op,args},...] runs several in one request."
)


class McpError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Server:
    def __init__(self, context: InstanceContext, *, authority: str = "observe", surface: str = "default") -> None:
        if authority not in operations.AUTHORITY_ORDER:
            raise ValueError(f"unknown authority: {authority}")
        self.context = context
        self.authority = authority
        self.surface = surface

    # ------------------------------------------------------------------ protocol

    def handle_line(self, line: str) -> dict | None:
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            return _error(None, -32700, f"parse error: {exc}")
        is_notification = isinstance(request, dict) and "id" not in request
        request_id = request.get("id") if isinstance(request, dict) else None
        try:
            result = self.dispatch(request)
        except McpError as exc:
            return None if is_notification else _error(request_id, exc.code, exc.message)
        except Exception as exc:  # never let one request kill the session
            return None if is_notification else _error(request_id, -32603, f"{type(exc).__name__}: {exc}")
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def dispatch(self, request: Any) -> dict:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            raise McpError(-32600, "invalid JSON-RPC 2.0 request")
        method = request.get("method")
        if not isinstance(method, str):
            raise McpError(-32600, "method is required")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            raise McpError(-32602, "params must be an object")
        if method == "initialize":
            return self.initialize(params)
        if method.startswith("notifications/"):
            return {}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.tool_descriptors()}
        if method == "tools/call":
            return self.call(params)
        raise McpError(-32601, f"unknown method: {method}")

    def initialize(self, params: dict) -> dict:
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return {
            "protocolVersion": version,
            "serverInfo": {"name": "helpers", "version": PRODUCT_VERSION},
            "capabilities": {"tools": {}},
            "instructions": (
                f"Local project tools for '{self.context.target_root.name}' (authority: "
                f"{self.authority}). Paths are relative to the project root. Orient with "
                "changes (what moved since last look) or outline/ls, then grep/read. "
                "run op=help lists everything else."
            ),
        }

    # ------------------------------------------------------------------ tools

    def tool_descriptors(self) -> list[dict]:
        descriptors: list[dict] = []
        if self.surface != "minimal":
            manifests = registry.discover(self.context)
            for name in HOT_TOOLS:
                op = operations.OPERATIONS.get(name)
                if op is not None and op.schema and operations.permits(self.authority, op.authority):
                    descriptors.append({"name": name, "description": op.summary,
                                        "inputSchema": _lean(op.schema)})
                    continue
                manifest = manifests.get(name)
                if manifest and operations.permits(self.authority, manifest.authority):
                    descriptors.append({
                        "name": name,
                        "description": manifest.description,
                        "inputSchema": _lean(manifest.input_schema),
                    })
        descriptors.append({
            "name": "run",
            "description": _RUN_DESCRIPTION,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "op": {"type": "string"},
                    "args": {"type": "object"},
                    "calls": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"op": {"type": "string"}, "args": {"type": "object"}},
                            "required": ["op"],
                        },
                    },
                },
            },
        })
        return descriptors

    def call(self, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            raise McpError(-32602, "tool name is required")
        if not isinstance(arguments, dict):
            raise McpError(-32602, "tool arguments must be an object")
        if name == "run":
            return self._run(arguments)
        if self.surface != "minimal" and name in HOT_TOOLS:
            ok, text = self._one(name, arguments)
            return _text_result(text, not ok)
        raise McpError(-32602, f"unknown tool: {name}")

    def _run(self, arguments: dict) -> dict:
        calls = arguments.get("calls")
        if calls is not None:
            if not isinstance(calls, list) or not calls:
                return _text_result("error: calls must be a non-empty array", True)
            chunks: list[str] = []
            failures = 0
            for index, item in enumerate(calls, start=1):
                if not isinstance(item, dict) or not isinstance(item.get("op"), str):
                    ok, text = False, "error: each call needs an op"
                else:
                    ok, text = self._one(item["op"], item.get("args") or {})
                failures += not ok
                label = item.get("op") if isinstance(item, dict) else "?"
                chunks.append(f"## {index}. {label}\n{text}")
            return _text_result("\n\n".join(chunks), failures == len(calls))
        op = arguments.get("op")
        if not isinstance(op, str) or not op:
            return _text_result('error: op is required (try op="help")', True)
        ok, text = self._one(op, arguments.get("args") or {})
        return _text_result(text, not ok)

    def _one(self, op: str, args: Any) -> tuple[bool, str]:
        try:
            if op == "help":
                target = args.get("op") if isinstance(args, dict) else None
                return True, operations.help_text(self.context, self.authority, target)
            kind, response = operations.execute(
                self.context, op, args,
                held=self.authority, client="mcp", timeout_seconds=300,
            )
        except Exception as exc:  # module errors become tool errors, not protocol errors
            return False, f"error[{type(exc).__name__}]: {exc}"
        ok = bool(response.get("ok", True))
        text = render.invocation(response) if kind == "tool" else render.projection(op, response)
        return ok, text


# Validation-only keywords: the control plane enforces them on every call, so listing them
# costs context without helping a model choose arguments.
_VALIDATION_ONLY = {"minLength", "maxLength", "minimum", "maximum", "additionalProperties"}


def _lean(schema: object) -> object:
    if isinstance(schema, dict):
        return {k: _lean(v) for k, v in schema.items() if k not in _VALIDATION_ONLY}
    if isinstance(schema, list):
        return [_lean(item) for item in schema]
    return schema


def _text_result(text: str, is_error: bool) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _error(request_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(
    context: InstanceContext,
    *,
    authority: str = "observe",
    surface: str = "default",
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    server = Server(context, authority=authority, surface=surface)
    reader = input_stream or sys.stdin
    writer = output_stream or sys.stdout
    while True:
        line = reader.readline()
        if not line:
            return 0
        if not line.strip():
            continue
        response = server.handle_line(line)
        if response is None:
            continue
        writer.write(json.dumps(response, separators=(",", ":"), ensure_ascii=False) + "\n")
        writer.flush()

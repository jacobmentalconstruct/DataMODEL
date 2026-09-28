from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import host, operations, registry, render
from .constants import AUTHORITY_ORDER
from .instance import default_archive, load, package

_AUTHORITIES = tuple(AUTHORITY_ORDER)
_COMMANDS = {"init", "status", "mcp", "mcp-config", "pack", "run"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="helpers",
        description="Any tool or op also works directly: helpers grep pattern=foo glob=*.py",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create this instance's identity (also automatic on first use)")
    pack = commands.add_parser("pack", help="zip the project without this instance's identity/state")
    pack.add_argument("dest", nargs="?", help="output .zip (default: <project>.zip beside the project)")
    commands.add_parser("status")
    mcp = commands.add_parser("mcp", help="serve MCP over stdio")
    mcp.add_argument("--authority", choices=_AUTHORITIES, default="observe")
    mcp.add_argument("--surface", choices=("default", "minimal"), default="default")
    mcp_config = commands.add_parser("mcp-config", help="print an .mcp.json server entry")
    mcp_config.add_argument("--authority", choices=_AUTHORITIES, default="apply")
    run = commands.add_parser("run", help="run any tool or op: run OP key=value ... (run help)")
    run.add_argument("op")
    run.add_argument("pairs", nargs="*", help="key=value (key=@- reads stdin)")
    run.add_argument("--args", help="JSON object of arguments (merged under key=value)")
    run.add_argument("--authority", choices=_AUTHORITIES, default="apply")
    run.add_argument("--json", action="store_true", help="print the full envelope")
    run.add_argument("--timeout", type=int, default=300)
    return parser


def _coerce(raw: str, declared: object) -> object:
    """key=value values: strings stay strings where the contract says string."""
    if raw == "@-":
        return sys.stdin.read()
    kinds = declared if isinstance(declared, list) else [declared]
    if "string" in kinds and "array" not in kinds:
        return raw
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    return raw if isinstance(value, str) else value


def _run_arguments(context, arguments) -> dict:
    parsed: dict = {}
    if arguments.args:
        loaded = json.loads(arguments.args)
        if not isinstance(loaded, dict):
            raise ValueError("--args must be a JSON object")
        parsed.update(loaded)
    tools = registry.discover(context)
    op = operations.OPERATIONS.get(arguments.op)
    if arguments.op in tools:
        properties = tools[arguments.op].input_schema.get("properties", {})
    elif op is not None and op.schema:
        properties = op.schema.get("properties", {})
    else:
        properties = {"limit": {"type": "integer"}, "overwrite": {"type": "boolean"}}
    for pair in arguments.pairs:
        key, separator, raw = pair.partition("=")
        if not separator or not key:
            raise ValueError(f"expected key=value, got {pair!r}")
        parsed[key] = _coerce(raw, properties.get(key, {}).get("type", "string"))
    return parsed


def _run(context, arguments) -> int:
    if arguments.op.startswith("op="):  # `run op=grep ...`, the form the MCP `run` tool uses
        arguments.op = arguments.op[3:]
    if arguments.op == "help":
        pairs = dict(pair.partition("=")[::2] for pair in arguments.pairs)
        print(operations.help_text(context, arguments.authority, pairs.get("op")))
        return 0
    kind, response = operations.execute(
        context,
        arguments.op,
        _run_arguments(context, arguments),
        held=arguments.authority,
        client="cli",
        timeout_seconds=arguments.timeout,
    )
    if arguments.json:
        print(render.compact(response))
    elif kind == "tool":
        print(render.invocation(response))
    else:
        print(render.projection(arguments.op, response))
    return 0 if response.get("ok", True) else 1


def main(instance_root: str | Path, argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and not argv[0].startswith("-") and argv[0] not in _COMMANDS:
        argv.insert(0, "run")  # shorthand: `helpers grep pattern=foo`
    root = Path(instance_root).resolve()
    try:
        arguments = _parser().parse_args(argv)
        created = not (root / "instance.json").exists()
        context = load(root, create_missing=True)  # a fresh unpacked copy initialises itself
        if arguments.command == "init":
            print(render.compact({**host.status(context), "initialized": created}))
            return 0
        if arguments.command == "pack":
            destination = arguments.dest or str(default_archive(root))
            print(render.compact(package(root, destination)))
            return 0
        if arguments.command == "status":
            print(render.compact(host.status(context)))
            return 0
        if arguments.command == "mcp-config":
            print(render.compact({"mcpServers": {"helpers": {
                "type": "stdio",
                "command": "python",
                "args": [".tools/bin/helpers.py", "mcp", "--authority", arguments.authority],
            }}}))
            return 0
        if arguments.command == "mcp":
            from . import mcp

            return mcp.serve(context, authority=arguments.authority, surface=arguments.surface)
        return _run(context, arguments)
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:  # all domain errors derive from these
        print(render.compact({"ok": False, "error": {"code": type(exc).__name__, "message": str(exc)}}))
        return 1


if __name__ == "__main__":
    sys.exit(main(Path(__file__).resolve().parents[1], sys.argv[1:]))
